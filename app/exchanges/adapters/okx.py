"""
OKX spot adapter, built against the official V5 API docs
(https://www.okx.com/docs-v5/en):
  - REST base:   https://www.okx.com
  - instruments: GET /api/v5/public/instruments?instType=SPOT
  - orderbook:   GET /api/v5/market/books?instId=...&sz=50  (REST fallback)
  - ticker:      GET /api/v5/market/ticker?instId=...
  - WS base:     wss://ws.okx.com:8443/ws/v5/public
  - channel:     "books5" - top-5 order book, re-pushed as a FULL snapshot on
    every change (unlike the deeper "books" channel, which requires
    client-side delta merging). We deliberately use books5 for the same
    reason as Binance's partial-depth stream: correctness with a simple,
    obviously-right merge rule (replace, not patch) beats a deeper book with
    more room for a subtle merge bug. 5 levels is enough to detect and size
    small-to-medium arbitrage opportunities; a "books" (deeper, delta-based)
    variant can be added later following the same snapshot/delta pattern
    used in bybit.py once it's been validated against live traffic.
  - ONE WebSocket connection is shared across every subscribed symbol - OKX
    accepts multiple {"channel","instId"} entries in a single `args` array,
    so adding a symbol sends an incremental subscribe on the existing
    connection (see _ws_common.py).

instId uses BASE-QUOTE (e.g. "BTC-USDT"), which SymbolNormalizer already
handles via its "-" separator rule.

No API key is required for any of the above (public market data).

Trading fees: OKX's account-specific fee tier requires authentication, out
of scope here. We use OKX's documented regular-tier default as an
approximation (see DEFAULT_MAKER_FEE/DEFAULT_TAKER_FEE below).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from app.exchanges.adapters._ws_common import MultiplexedOrderbookStream, chunked
from app.exchanges.base import ExchangeAdapter, ExchangeUnsupportedError, OrderBookCallback
from app.exchanges.symbol_normalizer import SymbolNormalizer
from app.models.market import (
    ExchangeConnectionStatus,
    ExchangeFees,
    ExchangeHealth,
    ExchangeMarket,
    MarketStatus,
    OrderBook,
    OrderBookLevel,
    Ticker,
)

logger = logging.getLogger(__name__)

REST_BASE = "https://www.okx.com"
WS_BASE = "wss://ws.okx.com:8443/ws/v5/public"

# OKX's documented regular-tier (non-VIP) spot rate. Approximation - see
# module docstring.
DEFAULT_MAKER_FEE = 0.0008
DEFAULT_TAKER_FEE = 0.0010

PING_INTERVAL = 20.0
MAX_ARGS_PER_SUBSCRIBE = 50  # keeps each frame well under OKX message-size limits
RECONNECT_MIN_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0


class OkxAdapter(ExchangeAdapter):
    name = "okx"
    is_supported = True

    def __init__(self) -> None:
        super().__init__()
        self._http = httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)
        self._normalizer = SymbolNormalizer()
        self._latest_books: dict[str, OrderBook] = {}
        self._markets_count = 0
        self._last_message_at: Optional[float] = None
        self._stream = MultiplexedOrderbookStream(
            url=WS_BASE,
            build_subscribe_message=self._build_subscribe_frames,
            on_message=self._handle_message,
            heartbeat_message=lambda: "ping",
            heartbeat_interval=PING_INTERVAL,
            exchange_label=self.name,
        )

    # -- Metadata -------------------------------------------------------------

    async def get_markets(self) -> list[ExchangeMarket]:
        resp = await self._http.get("/api/v5/public/instruments", params={"instType": "SPOT"})
        resp.raise_for_status()
        data = resp.json()

        markets: list[ExchangeMarket] = []
        for item in data.get("data", []):
            symbol = item["instId"]  # already "BASE-QUOTE"
            try:
                normalized = self._normalizer.normalize(self.name, symbol)
            except Exception:  # noqa: BLE001
                logger.debug("Skipping unnormalizable OKX symbol %s", symbol)
                continue

            status = MarketStatus.TRADING if item.get("state") == "live" else MarketStatus.HALTED
            tick_size = float(item.get("tickSz", 0.0) or 0.0)
            step_size = float(item.get("lotSz", 0.0) or 0.0)

            market = ExchangeMarket(
                exchange=self.name,
                symbol=symbol,
                base_asset=normalized.base_asset,
                quote_asset=normalized.quote_asset,
                market_type="spot",
                status=status,
                price_precision=len(item.get("tickSz", "0.01").split(".")[-1]),
                quantity_precision=len(item.get("lotSz", "0.000001").split(".")[-1]),
                tick_size=tick_size,
                step_size=step_size,
                min_quantity=float(item.get("minSz", 0.0) or 0.0),
                min_notional=0.0,  # OKX doesn't publish a separate min notional for spot
            )
            markets.append(market)

        self._markets_count = len(markets)
        return markets

    async def get_fees(self, symbol: Optional[str] = None) -> ExchangeFees:
        return ExchangeFees(
            exchange=self.name, symbol=symbol, maker_fee=DEFAULT_MAKER_FEE, taker_fee=DEFAULT_TAKER_FEE
        )

    async def get_exchange_status(self) -> ExchangeHealth:
        latency_ms = None
        if self._last_message_at is not None:
            latency_ms = max(0.0, (time.time() - self._last_message_at) * 1000)
        status = ExchangeConnectionStatus.CONNECTED if self._connected else ExchangeConnectionStatus.DISCONNECTED
        return ExchangeHealth(
            exchange=self.name,
            status=status,
            markets_count=self._markets_count,
            last_update=self._last_message_at,
            latency_ms=latency_ms,
        )

    # -- REST market data -------------------------------------------------------

    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        if symbol in self._latest_books:
            return self._latest_books[symbol]
        resp = await self._http.get(
            "/api/v5/market/books", params={"instId": symbol, "sz": min(depth, 400)}
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            raise KeyError(f"No order book for {symbol} on OKX")
        item = items[0]
        now = time.time()
        return OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q, *_ in item.get("bids", [])],
            asks=[OrderBookLevel(float(p), float(q)) for p, q, *_ in item.get("asks", [])],
            received_at=now,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        resp = await self._http.get("/api/v5/market/ticker", params={"instId": symbol})
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            raise KeyError(f"No ticker for {symbol} on OKX")
        item = items[0]
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=float(item.get("bidPx", 0.0) or 0.0),
            bid_volume=float(item.get("bidSz", 0.0) or 0.0),
            ask=float(item.get("askPx", 0.0) or 0.0),
            ask_volume=float(item.get("askSz", 0.0) or 0.0),
            timestamp=time.time(),
        )

    # -- WebSocket market data --------------------------------------------------

    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        self._orderbook_subscribers.setdefault(symbol, []).append(callback)
        self._stream.add_symbol(symbol)

    async def unsubscribe_orderbook(self, symbol: str) -> None:
        self._orderbook_subscribers.pop(symbol, None)
        self._stream.remove_symbol(symbol)

    async def connect(self) -> None:
        try:
            resp = await self._http.get("/api/v5/public/time", timeout=5.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnsupportedError(f"OKX REST reachability check failed: {exc}") from exc
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        await self._stream.stop()
        await self._http.aclose()

    # -- Internal: multiplexed WebSocket (one connection for all symbols) -------

    def _build_subscribe_frames(self, symbols: list[str]) -> list[str]:
        args = [{"channel": "books5", "instId": s} for s in symbols]
        return [json.dumps({"op": "subscribe", "args": chunk}) for chunk in chunked(args, MAX_ARGS_PER_SUBSCRIBE)]

    def _handle_message(self, raw: str) -> None:
        if raw == "pong":
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if "event" in data:
            return  # subscription ack/error frames

        arg = data.get("arg", {})
        if arg.get("channel") != "books5":
            return
        symbol = arg.get("instId")
        if not symbol:
            return

        items = data.get("data", [])
        if not items:
            return
        item = items[0]

        now = time.time()
        self._last_message_at = now
        book = OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q, *_ in item.get("bids", [])],
            asks=[OrderBookLevel(float(p), float(q)) for p, q, *_ in item.get("asks", [])],
            received_at=now,
        )
        self._latest_books[symbol] = book
        for cb in self._orderbook_subscribers.get(symbol, []):
            cb(book)
