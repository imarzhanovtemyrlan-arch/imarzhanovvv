"""
Binance spot adapter - first real (non-mock) exchange integration.

Built against the OFFICIAL Binance Spot API docs
(https://github.com/binance/binance-spot-api-docs):
  - REST base:      https://api.binance.com
  - exchangeInfo:   GET /api/v3/exchangeInfo            (markets/filters)
  - depth snapshot: GET /api/v3/depth                    (REST fallback/recovery)
  - book ticker:    GET /api/v3/ticker/bookTicker         (best bid/ask REST fallback)
  - WS base:        wss://stream.binance.com:9443/stream  (combined-stream mode -
    connecting here, rather than to /ws/<streamName>, makes Binance wrap every
    push as {"stream": "<n>", "data": {...}} regardless of how many
    streams are active, per Binance's docs: "The combined property is set
    to ... true when connecting using /stream/". This lets ONE connection
    carry every subscribed symbol instead of one connection per symbol -
    see app/exchanges/adapters/_ws_common.py for why that matters.)
  - partial depth stream: <symbol>@depth<levels>@100ms   (already-sorted top-N book,
    resent on every update - no client-side diff merging required, which keeps
    reconnect/recovery simple and correctness-focused per spec priorities)
  - streams are added to the live connection via the documented SUBSCRIBE
    control message ({"method": "SUBSCRIBE", "params": [...], "id": N})

No API key is required for any of the above - all are public market-data
endpoints, consistent with "monitoring only" mode (spec section 21).

Trading fees: Binance's authenticated commission endpoint requires signed
requests, which are out of scope for monitoring-only mode. We use Binance's
published default public spot maker/taker rate (0.1%/0.1%) as a documented
default and flag it as such; get_fees() results should be treated as an
approximation until a user connects their own account in a future version.
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

REST_BASE = "https://api.binance.com"
WS_BASE = "wss://stream.binance.com:9443/stream"

# Binance's published default public spot commission rate. NOT fetched via
# authenticated endpoint (see module docstring) - treat as an approximation.
DEFAULT_MAKER_FEE = 0.001
DEFAULT_TAKER_FEE = 0.001

DEPTH_STREAM_LEVELS = 20
MAX_STREAMS_PER_SUBSCRIBE = 100  # Binance limits control messages to 5/s per connection; batch streams


class BinanceAdapter(ExchangeAdapter):
    name = "binance"
    is_supported = True

    def __init__(self) -> None:
        super().__init__()
        self._http = httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)
        self._normalizer = SymbolNormalizer()
        self._latest_books: dict[str, OrderBook] = {}
        self._markets_cache: dict[str, ExchangeMarket] = {}
        self._last_message_at: Optional[float] = None
        self._markets_count = 0
        self._sub_id_counter = 0
        self._stream = MultiplexedOrderbookStream(
            url=WS_BASE,
            build_subscribe_message=self._build_subscribe_frames,
            build_unsubscribe_message=self._build_unsubscribe_frames,
            on_message=self._handle_depth_message,
            exchange_label=self.name,
        )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def get_markets(self) -> list[ExchangeMarket]:
        resp = await self._http.get("/api/v3/exchangeInfo")
        resp.raise_for_status()
        data = resp.json()

        markets: list[ExchangeMarket] = []
        for s in data.get("symbols", []):
            if s.get("status") != "TRADING" and s.get("status") != "HALT":
                continue
            if not s.get("isSpotTradingAllowed", True):
                continue
            symbol = s["symbol"]
            try:
                normalized = self._normalizer.normalize(self.name, symbol)
            except Exception:  # noqa: BLE001
                logger.debug("Skipping unnormalizable Binance symbol %s", symbol)
                continue

            tick_size = 0.0
            step_size = 0.0
            min_qty = 0.0
            min_notional = 0.0
            for f in s.get("filters", []):
                ftype = f.get("filterType")
                if ftype == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0.0))
                elif ftype == "LOT_SIZE":
                    step_size = float(f.get("stepSize", 0.0))
                    min_qty = float(f.get("minQty", 0.0))
                elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = float(f.get("minNotional", f.get("minNotional", 0.0)) or 0.0)

            status = MarketStatus.TRADING if s.get("status") == "TRADING" else MarketStatus.HALTED

            market = ExchangeMarket(
                exchange=self.name,
                symbol=symbol,
                base_asset=normalized.base_asset,
                quote_asset=normalized.quote_asset,
                market_type="spot",
                status=status,
                price_precision=s.get("quoteAssetPrecision", 8),
                quantity_precision=s.get("baseAssetPrecision", 8),
                tick_size=tick_size or 10 ** -s.get("quoteAssetPrecision", 8),
                step_size=step_size or 10 ** -s.get("baseAssetPrecision", 8),
                min_quantity=min_qty,
                min_notional=min_notional,
            )
            markets.append(market)
            self._markets_cache[symbol] = market

        self._markets_count = len(markets)
        return markets

    async def get_fees(self, symbol: Optional[str] = None) -> ExchangeFees:
        # See module docstring: authenticated commission endpoint is out of
        # scope for monitoring-only mode, so this is Binance's documented
        # default public rate, not an account-specific fetch.
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

    # ------------------------------------------------------------------
    # REST market data (snapshot / fallback / recovery, spec section 5)
    # ------------------------------------------------------------------

    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        if symbol in self._latest_books:
            return self._latest_books[symbol]
        # REST fallback if no live WS data yet.
        limit = min(max(depth, 5), 1000)
        resp = await self._http.get("/api/v3/depth", params={"symbol": symbol, "limit": limit})
        resp.raise_for_status()
        data = resp.json()
        now = time.time()
        return OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])],
            sequence=data.get("lastUpdateId"),
            received_at=now,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        resp = await self._http.get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=float(data["bidPrice"]),
            bid_volume=float(data["bidQty"]),
            ask=float(data["askPrice"]),
            ask_volume=float(data["askQty"]),
            timestamp=time.time(),
        )

    # ------------------------------------------------------------------
    # WebSocket market data (primary source, spec section 5)
    # ------------------------------------------------------------------

    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        self._orderbook_subscribers.setdefault(symbol, []).append(callback)
        self._stream.add_symbol(symbol)

    async def unsubscribe_orderbook(self, symbol: str) -> None:
        self._orderbook_subscribers.pop(symbol, None)
        self._latest_books.pop(symbol, None)
        self._stream.remove_symbol(symbol)

    async def connect(self) -> None:
        # Verify basic reachability before declaring this exchange usable;
        # if this fails we mark UNSUPPORTED rather than silently pretending
        # to be connected (spec section 4).
        try:
            resp = await self._http.get("/api/v3/ping", timeout=5.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnsupportedError(f"Binance REST ping failed: {exc}") from exc
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        await self._stream.stop()
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internal: multiplexed WebSocket (one connection for all symbols)
    # ------------------------------------------------------------------

    def _build_subscribe_frames(self, symbols: list[str]) -> list[str]:
        params = [f"{s.lower()}@depth{DEPTH_STREAM_LEVELS}@100ms" for s in symbols]
        frames = []
        for chunk in chunked(params, MAX_STREAMS_PER_SUBSCRIBE):
            self._sub_id_counter += 1
            frames.append(json.dumps({"method": "SUBSCRIBE", "params": chunk, "id": self._sub_id_counter}))
        return frames

    def _build_unsubscribe_frames(self, symbols: list[str]) -> list[str]:
        params = [f"{s.lower()}@depth{DEPTH_STREAM_LEVELS}@100ms" for s in symbols]
        frames = []
        for chunk in chunked(params, MAX_STREAMS_PER_SUBSCRIBE):
            self._sub_id_counter += 1
            frames.append(json.dumps({
                "method": "UNSUBSCRIBE",
                "params": chunk,
                "id": self._sub_id_counter,
            }))
        return frames

    def _handle_depth_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Malformed Binance WS payload")
            return

        # Subscribe/unsubscribe acknowledgements look like {"result": null, "id": N}.
        if "stream" not in data:
            return

        stream_name = data["stream"]
        symbol = stream_name.split("@")[0].upper()
        payload = data.get("data", {})

        now = time.time()
        self._last_message_at = now
        book = OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q in payload.get("bids", [])],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in payload.get("asks", [])],
            sequence=payload.get("lastUpdateId"),
            received_at=now,
        )
        self._latest_books[symbol] = book
        for cb in self._orderbook_subscribers.get(symbol, []):
            cb(book)