"""
MEXC spot adapter, built against the official spot v3 API docs
(https://mexcdevelop.github.io/apidocs/spot_v3_en/):
  - REST base:    https://api.mexc.com
  - exchangeInfo: GET /api/v3/exchangeInfo
  - depth:        GET /api/v3/depth?symbol=...&limit=...
  - bookTicker:   GET /api/v3/ticker/bookTicker?symbol=...

Live order books come from MEXC's public WebSocket (Protocol Buffers),
see mexc_ws.py. REST is used for market metadata and one-off requests.

Trading fees: no public commission endpoint; uses MEXC's documented
default spot rate as an approximation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from app.exchanges.base import ExchangeAdapter, ExchangeUnsupportedError, OrderBookCallback
from app.exchanges.symbol_normalizer import SymbolNormalizer
from app.exchanges.adapters.mexc_ws import MexcWebSocketFeed
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

REST_BASE = "https://api.mexc.com"

DEFAULT_MAKER_FEE = 0.0
DEFAULT_TAKER_FEE = 0.0005  # MEXC's advertised spot base rate is unusually low; treat as approximate

REQUESTS_PER_SECOND = 20.0
MAX_CONCURRENT_REQUESTS = 5
REQUEST_INTERVAL_SECONDS = 1.0 / REQUESTS_PER_SECOND
RETRY_MIN_DELAY = 1.0
RETRY_MAX_DELAY = 30.0


class MexcAdapter(ExchangeAdapter):
    name = "mexc"
    is_supported = True

    def __init__(self) -> None:
        super().__init__()
        self._http = httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)
        self._normalizer = SymbolNormalizer()
        self._latest_books: dict[str, OrderBook] = {}
        self._subscribed_symbols: set[str] = set()
        self._poll_loop_task: Optional[asyncio.Task] = None
        self._request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._markets_count = 0
        self._last_message_at: Optional[float] = None
        self._feed = MexcWebSocketFeed(self._on_ws_book)

    # -- Metadata ---------------------------------------------------------

    async def get_markets(self) -> list[ExchangeMarket]:
        resp = await self._http.get("/api/v3/exchangeInfo")
        resp.raise_for_status()
        data = resp.json()

        markets: list[ExchangeMarket] = []
        for s in data.get("symbols", []):
            symbol = s["symbol"]
            try:
                normalized = self._normalizer.normalize(self.name, symbol)
            except Exception:  # noqa: BLE001
                logger.debug("Skipping unnormalizable MEXC symbol %s", symbol)
                continue

            tick_size = 0.0
            step_size = 0.0
            min_notional = 0.0
            for f in s.get("filters", []):
                ftype = f.get("filterType")
                if ftype == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0.0) or 0.0)
                elif ftype == "LOT_SIZE":
                    step_size = float(f.get("stepSize", 0.0) or 0.0)
                elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = float(f.get("minNotional", 0.0) or 0.0)

            status = MarketStatus.TRADING if s.get("status") in ("ENABLED", "1") else MarketStatus.HALTED
            quote_precision = s.get("quoteAssetPrecision", 8)
            base_precision = s.get("baseAssetPrecision", 8)

            market = ExchangeMarket(
                exchange=self.name,
                symbol=symbol,
                base_asset=normalized.base_asset,
                quote_asset=normalized.quote_asset,
                market_type="spot",
                status=status,
                price_precision=quote_precision,
                quantity_precision=base_precision,
                tick_size=tick_size or 10 ** -quote_precision,
                step_size=step_size or 10 ** -base_precision,
                min_quantity=0.0,
                min_notional=min_notional,
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
            reason=None,
        )

    # -- REST market data ---------------------------------------------------

    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        resp = await self._http.get("/api/v3/depth", params={"symbol": symbol, "limit": min(max(depth, 5), 1000)})
        resp.raise_for_status()
        data = resp.json()
        now = time.time()
        book = OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])],
            asks=[OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])],
            received_at=now,
        )
        self._latest_books[symbol] = book
        return book

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

    # -- Live order books via WebSocket -------------------------------------

    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        self._orderbook_subscribers.setdefault(symbol, []).append(callback)
        self._subscribed_symbols.add(symbol)
        await self._feed.add_symbol(symbol)

    async def unsubscribe_orderbook(self, symbol: str) -> None:
        self._orderbook_subscribers.pop(symbol, None)
        self._subscribed_symbols.discard(symbol)
        self._latest_books.pop(symbol, None)
        await self._feed.remove_symbol(symbol)

    def _on_ws_book(self, symbol: str, bids: list, asks: list) -> None:
        now = time.time()
        self._last_message_at = now
        book = OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(p, q) for p, q in bids],
            asks=[OrderBookLevel(p, q) for p, q in asks],
            received_at=now,
        )
        self._latest_books[symbol] = book
        for cb in self._orderbook_subscribers.get(symbol, []):
            cb(book)

    async def connect(self) -> None:
        try:
            resp = await self._http.get("/api/v3/ping", timeout=5.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnsupportedError(f"MEXC REST ping failed: {exc}") from exc
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        await self._feed.stop()
        if self._poll_loop_task is not None:
            self._poll_loop_task.cancel()
            self._poll_loop_task = None
        await self._http.aclose()

    # -- Old REST polling (kept as a fallback, currently not used) ------------

    async def _poll_one_symbol(self, symbol: str) -> None:
        async with self._request_semaphore:
            try:
                book = await self.get_orderbook(symbol)
                self._last_message_at = time.time()
                for cb in self._orderbook_subscribers.get(symbol, []):
                    cb(book)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("MEXC REST poll failed for %s: %s", symbol, exc)

    async def _poll_loop(self) -> None:
        while True:
            symbols = list(self._subscribed_symbols)
            if not symbols:
                await asyncio.sleep(0.5)
                continue

            for symbol in symbols:
                if symbol not in self._subscribed_symbols:
                    continue
                await self._poll_one_symbol(symbol)
                await asyncio.sleep(REQUEST_INTERVAL_SECONDS)