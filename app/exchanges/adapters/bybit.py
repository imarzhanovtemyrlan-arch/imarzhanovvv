"""
Bybit spot adapter, built against the official V5 unified API docs
(https://bybit-exchange.github.io/docs/v5/intro):
  - REST base:      https://api.bybit.com
  - instruments:    GET /v5/market/instruments-info?category=spot
  - orderbook REST: GET /v5/market/orderbook?category=spot&symbol=...&limit=50
  - tickers REST:   GET /v5/market/tickers?category=spot&symbol=...
  - WS base:        wss://stream.bybit.com/v5/public/spot
  - orderbook topic: orderbook.50.<symbol> - first push is a full snapshot,
    subsequent pushes are deltas (price levels with size "0" mean "remove
    this level"). We maintain the local book exactly per that documented
    contract rather than treating every push as a full replacement, since
    Bybit's docs are explicit that deltas must be merged, not swapped in
    (unlike Binance's partial-depth stream, which resends the full top-N
    every time).
  - Client must send {"op": "ping"} roughly every 20s to keep the
    connection alive (per Bybit's documented recommendation).
  - ONE WebSocket connection is shared across every subscribed symbol -
    Bybit's `subscribe` accepts multiple topics in a single `args` array,
    so adding a symbol sends an incremental subscribe on the existing
    connection rather than opening a new one (see _ws_common.py; opening
    one connection per symbol was an earlier bug that could exceed
    exchange-side per-IP connection limits once many symbols matched).

All of the above are public, unauthenticated endpoints - no API key needed
for monitoring mode.

Trading fees: Bybit's account-specific commission requires an authenticated
endpoint, out of scope here (see module docstring in binance.py for the same
reasoning). We use Bybit's documented default VIP0 spot rate as an
approximation.
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

REST_BASE = "https://api.bybit.com"
WS_BASE = "wss://stream.bybit.com/v5/public/spot"

DEFAULT_MAKER_FEE = 0.001
DEFAULT_TAKER_FEE = 0.001

ORDERBOOK_DEPTH = 50
MAX_ARGS_PER_SUBSCRIBE = 10  # Bybit caps topics per spot subscribe request (as documented; chunking is harmless if higher)
PING_INTERVAL = 20.0
RECONNECT_MIN_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0


class LocalOrderBookState:
    """Maintains one side (bids or asks) of a book as price->quantity, so
    Bybit's snapshot+delta protocol can be applied correctly."""

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def apply_snapshot(self, bids: list[list[str]], asks: list[list[str]]) -> None:
        self.bids = {float(p): float(q) for p, q in bids}
        self.asks = {float(p): float(q) for p, q in asks}

    def apply_delta(self, bids: list[list[str]], asks: list[list[str]]) -> None:
        merge_levels(self.bids, bids)
        merge_levels(self.asks, asks)

    def to_levels(self, depth: int = ORDERBOOK_DEPTH) -> tuple[list[OrderBookLevel], list[OrderBookLevel]]:
        bid_levels = [
            OrderBookLevel(p, q) for p, q in sorted(self.bids.items(), key=lambda x: -x[0])[:depth] if q > 0
        ]
        ask_levels = [
            OrderBookLevel(p, q) for p, q in sorted(self.asks.items(), key=lambda x: x[0])[:depth] if q > 0
        ]
        return bid_levels, ask_levels


def merge_levels(book_side: dict[float, float], updates: list[list[str]]) -> None:
    """Apply a Bybit delta update to one side of a local book in place.

    A quantity of "0" means the price level should be removed - this is the
    standard exchange orderbook delta convention documented by Bybit.
    Extracted as a pure function so it's directly unit-testable without any
    network/WebSocket dependency.
    """
    for price_str, qty_str in updates:
        price = float(price_str)
        qty = float(qty_str)
        if qty == 0:
            book_side.pop(price, None)
        else:
            book_side[price] = qty


class BybitAdapter(ExchangeAdapter):
    name = "bybit"
    is_supported = True

    def __init__(self) -> None:
        super().__init__()
        self._http = httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)
        self._normalizer = SymbolNormalizer()
        self._latest_books: dict[str, OrderBook] = {}
        self._local_state: dict[str, LocalOrderBookState] = {}
        self._markets_count = 0
        self._last_message_at: Optional[float] = None
        self._stream = MultiplexedOrderbookStream(
            url=WS_BASE,
            build_subscribe_message=self._build_subscribe_frames,
            on_message=self._handle_message,
            heartbeat_message=lambda: json.dumps({"op": "ping"}),
            heartbeat_interval=PING_INTERVAL,
            exchange_label=self.name,
        )

    # -- Metadata -----------------------------------------------------------

    async def get_markets(self) -> list[ExchangeMarket]:
        resp = await self._http.get("/v5/market/instruments-info", params={"category": "spot"})
        resp.raise_for_status()
        data = resp.json()

        markets: list[ExchangeMarket] = []
        for item in data.get("result", {}).get("list", []):
            symbol = item["symbol"]
            try:
                normalized = self._normalizer.normalize(self.name, symbol)
            except Exception:  # noqa: BLE001
                logger.debug("Skipping unnormalizable Bybit symbol %s", symbol)
                continue

            lot_filter = item.get("lotSizeFilter", {})
            price_filter = item.get("priceFilter", {})
            status = MarketStatus.TRADING if item.get("status") == "Trading" else MarketStatus.HALTED

            market = ExchangeMarket(
                exchange=self.name,
                symbol=symbol,
                base_asset=normalized.base_asset,
                quote_asset=normalized.quote_asset,
                market_type="spot",
                status=status,
                price_precision=len(price_filter.get("tickSize", "0.01").split(".")[-1]),
                quantity_precision=len(lot_filter.get("basePrecision", "0.000001").split(".")[-1]),
                tick_size=float(price_filter.get("tickSize", 0.0) or 0.0),
                step_size=float(lot_filter.get("basePrecision", 0.0) or 0.0),
                min_quantity=float(lot_filter.get("minOrderQty", 0.0) or 0.0),
                min_notional=float(lot_filter.get("minOrderAmt", 0.0) or 0.0),
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

    # -- REST market data -----------------------------------------------------

    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        if symbol in self._latest_books:
            return self._latest_books[symbol]
        resp = await self._http.get(
            "/v5/market/orderbook", params={"category": "spot", "symbol": symbol, "limit": min(depth, 50)}
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        now = time.time()
        return OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q in result.get("b", [])],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in result.get("a", [])],
            sequence=result.get("u"),
            received_at=now,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        resp = await self._http.get("/v5/market/tickers", params={"category": "spot", "symbol": symbol})
        resp.raise_for_status()
        items = resp.json().get("result", {}).get("list", [])
        if not items:
            raise KeyError(f"No ticker for {symbol} on Bybit")
        item = items[0]
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=float(item.get("bid1Price", 0.0) or 0.0),
            bid_volume=float(item.get("bid1Size", 0.0) or 0.0),
            ask=float(item.get("ask1Price", 0.0) or 0.0),
            ask_volume=float(item.get("ask1Size", 0.0) or 0.0),
            timestamp=time.time(),
        )

    # -- WebSocket market data ------------------------------------------------

    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        self._orderbook_subscribers.setdefault(symbol, []).append(callback)
        if symbol not in self._local_state:
            self._local_state[symbol] = LocalOrderBookState()
        self._stream.add_symbol(symbol)

    async def unsubscribe_orderbook(self, symbol: str) -> None:
        self._orderbook_subscribers.pop(symbol, None)
        self._stream.remove_symbol(symbol)
        self._local_state.pop(symbol, None)

    async def connect(self) -> None:
        try:
            resp = await self._http.get("/v5/market/time", timeout=5.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnsupportedError(f"Bybit REST reachability check failed: {exc}") from exc
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        await self._stream.stop()
        await self._http.aclose()

    # -- Internal: multiplexed WebSocket (one connection for all symbols) -----

    def _build_subscribe_frames(self, symbols: list[str]) -> list[str]:
        args = [f"orderbook.{ORDERBOOK_DEPTH}.{s}" for s in symbols]
        return [json.dumps({"op": "subscribe", "args": chunk}) for chunk in chunked(args, MAX_ARGS_PER_SUBSCRIBE)]

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if data.get("op") == "pong" or "success" in data:
            return  # subscription ack / pong - nothing to process

        topic = data.get("topic", "")
        if not topic.startswith("orderbook."):
            return
        # topic format: "orderbook.<depth>.<symbol>"
        symbol = topic.split(".", 2)[-1]

        payload = data.get("data", {})
        msg_type = data.get("type")
        state = self._local_state.get(symbol)
        if state is None:
            # Message for a symbol we're not (or no longer) tracking locally -
            # can happen briefly around unsubscribe; safe to ignore.
            return

        if msg_type == "snapshot":
            state.apply_snapshot(payload.get("b", []), payload.get("a", []))
        elif msg_type == "delta":
            state.apply_delta(payload.get("b", []), payload.get("a", []))
        else:
            return

        now = time.time()
        self._last_message_at = now
        bids, asks = state.to_levels()
        book = OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=bids,
            asks=asks,
            sequence=payload.get("u"),
            received_at=now,
        )
        self._latest_books[symbol] = book
        for cb in self._orderbook_subscribers.get(symbol, []):
            cb(book)
