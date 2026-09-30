"""
Gate.io spot adapter, built against the official V4 API docs
(https://www.gate.com/docs/developers/apiv4/en/):
  - REST base:    https://api.gateio.ws/api/v4
  - currency_pairs: GET /spot/currency_pairs
  - order_book:     GET /spot/order_book?currency_pair=...  (REST fallback)
  - tickers:         GET /spot/tickers?currency_pair=...
  - WS base:         wss://api.gateio.ws/ws/v4
  - channel "spot.order_book": a LIMITED-DEPTH, full-snapshot channel (as
    opposed to "spot.order_book_update", which is incremental and requires
    client-side delta merging). We use the snapshot channel for the same
    reason as Binance/OKX: correctness with a trivial "replace, don't
    patch" merge rule.
  - Client must periodically send a `spot.ping` channel message to keep
    the connection alive; server replies with `spot.pong`.
  - ONE WebSocket connection is shared across every subscribed symbol
    (Gate.io's subscribe frame only takes one currency pair each, so we
    send one frame per symbol over that single connection, rather than
    opening a new connection per symbol - see _ws_common.py).

Symbols are "BASE_QUOTE" (e.g. "BTC_USDT"), which SymbolNormalizer already
handles via its "_" separator rule.

No API key required for any of the above (public market data).

Trading fees: Gate.io's account-specific fee tier requires authentication;
we use Gate.io's documented default (non-VIP) spot rate as an
approximation.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from app.exchanges.adapters._ws_common import MultiplexedOrderbookStream
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

REST_BASE = "https://api.gateio.ws/api/v4"
WS_BASE = "wss://api.gateio.ws/ws/v4/"

# Gate.io's documented default (non-VIP) spot maker/taker rate. Approximation
# - see module docstring.
DEFAULT_MAKER_FEE = 0.002
DEFAULT_TAKER_FEE = 0.002

ORDERBOOK_LEVEL = "20"
ORDERBOOK_INTERVAL = "100ms"
PING_INTERVAL = 20.0
# Gate takes ONE pair per subscribe frame, so 1000 pairs = 1000 frames.
# 0.25s/frame (the Binance-safe default) meant ~4 minutes of subscribing.
CONTROL_INTERVAL = 0.05
RECONNECT_MIN_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0


class GateAdapter(ExchangeAdapter):
    name = "gate"
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
            heartbeat_message=lambda: json.dumps({"time": int(time.time()), "channel": "spot.ping"}),
            heartbeat_interval=PING_INTERVAL,
            exchange_label=self.name,
            control_interval=CONTROL_INTERVAL,
        )

    # -- Metadata ---------------------------------------------------------

    async def get_markets(self) -> list[ExchangeMarket]:
        resp = await self._http.get("/spot/currency_pairs")
        resp.raise_for_status()
        data = resp.json()

        markets: list[ExchangeMarket] = []
        for item in data:
            symbol = item["id"]  # "BASE_QUOTE"
            try:
                normalized = self._normalizer.normalize(self.name, symbol)
            except Exception:  # noqa: BLE001
                logger.debug("Skipping unnormalizable Gate.io symbol %s", symbol)
                continue

            status = MarketStatus.TRADING if item.get("trade_status") == "tradable" else MarketStatus.HALTED
            precision = int(item.get("precision", 6))
            amount_precision = int(item.get("amount_precision", 6))

            market = ExchangeMarket(
                exchange=self.name,
                symbol=symbol,
                base_asset=normalized.base_asset,
                quote_asset=normalized.quote_asset,
                market_type="spot",
                status=status,
                price_precision=precision,
                quantity_precision=amount_precision,
                tick_size=10 ** -precision,
                step_size=10 ** -amount_precision,
                min_quantity=float(item.get("min_base_amount", 0.0) or 0.0),
                min_notional=float(item.get("min_quote_amount", 0.0) or 0.0),
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

    # -- REST market data ---------------------------------------------------

    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        if symbol in self._latest_books:
            return self._latest_books[symbol]
        resp = await self._http.get(
            "/spot/order_book", params={"currency_pair": symbol, "limit": min(depth, 100)}
        )
        resp.raise_for_status()
        data = resp.json()
        now = time.time()
        return OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])],
            sequence=data.get("id"),
            received_at=now,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        resp = await self._http.get("/spot/tickers", params={"currency_pair": symbol})
        resp.raise_for_status()
        items = resp.json()
        if not items:
            raise KeyError(f"No ticker for {symbol} on Gate.io")
        item = items[0]
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=float(item.get("highest_bid", 0.0) or 0.0),
            bid_volume=0.0,  # Gate.io's tickers endpoint doesn't publish bid/ask size
            ask=float(item.get("lowest_ask", 0.0) or 0.0),
            ask_volume=0.0,
            timestamp=time.time(),
        )

    # -- WebSocket market data ------------------------------------------------

    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        self._orderbook_subscribers.setdefault(symbol, []).append(callback)
        self._stream.add_symbol(symbol)

    async def unsubscribe_orderbook(self, symbol: str) -> None:
        self._orderbook_subscribers.pop(symbol, None)
        self._latest_books.pop(symbol, None)
        self._stream.remove_symbol(symbol)

    async def connect(self) -> None:
        try:
            resp = await self._http.get("/spot/time", timeout=5.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnsupportedError(f"Gate.io REST reachability check failed: {exc}") from exc
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        await self._stream.stop()
        await self._http.aclose()

    # -- Internal: multiplexed WebSocket (one connection for all symbols) -------
    #
    # NOTE: unlike Binance/Bybit/OKX, Gate.io's "subscribe" payload takes
    # exactly ONE currency pair per frame (there's no documented array form
    # for spot.order_book), so build_subscribe_frames returns one frame per
    # symbol. They're all sent over the SAME shared connection though - this
    # is still one WebSocket per exchange, not one per symbol.

    def _build_subscribe_frames(self, symbols: list[str]) -> list[str]:
        return [
            json.dumps(
                {
                    "time": int(time.time()),
                    "channel": "spot.order_book",
                    "event": "subscribe",
                    "payload": [symbol, ORDERBOOK_LEVEL, ORDERBOOK_INTERVAL],
                }
            )
            for symbol in symbols
        ]

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if data.get("channel") == "spot.pong":
            return
        if data.get("channel") != "spot.order_book" or data.get("event") != "update":
            return

        result = data.get("result", {})
        symbol = result.get("s")
        if not symbol:
            logger.debug("Gate.io order_book push missing symbol field 's', dropping")
            return

        bids = result.get("bids", [])
        asks = result.get("asks", [])
        if not bids and not asks:
            return

        now = time.time()
        self._last_message_at = now
        book = OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q in bids],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in asks],
            sequence=result.get("id"),
            received_at=now,
        )
        self._latest_books[symbol] = book
        for cb in self._orderbook_subscribers.get(symbol, []):
            cb(book)