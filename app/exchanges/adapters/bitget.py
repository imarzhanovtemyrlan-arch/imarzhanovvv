"""
Bitget spot adapter, built against the official Bitget API docs
(https://www.bitget.com/api-doc):
  - REST base:   https://api.bitget.com
  - markets:     GET /api/v2/spot/public/symbols
  - WS base:     wss://ws.bitget.com/v2/ws/public
  - channel:     "books5" (instType SPOT) - 5 levels, FULL snapshot every push
  - heartbeat:   plain string "ping" every 30s, server replies "pong"
  - limits:      max 1000 channels per connection, 240 subscribe requests
    per hour per connection, forced disconnect every 24h.

Base and quote come from the exchange's own baseCoin/quoteCoin fields.
Order books and tickers are served from the WebSocket cache.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from app.exchanges.adapters._ws_common import MultiplexedOrderbookStream, chunked
from app.exchanges.base import ExchangeAdapter, ExchangeUnsupportedError, OrderBookCallback
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

REST_BASE = "https://api.bitget.com"
WS_BASE = "wss://ws.bitget.com/v2/ws/public"

DEFAULT_MAKER_FEE = 0.001
DEFAULT_TAKER_FEE = 0.001

PING_INTERVAL = 25.0
MAX_ARGS_PER_FRAME = 50


class BitgetAdapter(ExchangeAdapter):
    name = "bitget"
    is_supported = True

    def __init__(self) -> None:
        super().__init__()
        self._http = httpx.AsyncClient(base_url=REST_BASE, timeout=15.0)
        self._latest_books: dict[str, OrderBook] = {}
        self._fees: dict[str, tuple[float, float]] = {}
        self._markets_count = 0
        self._last_message_at: Optional[float] = None
        self._stream = MultiplexedOrderbookStream(
            url=WS_BASE,
            build_subscribe_message=self._build_subscribe_frames,
            build_unsubscribe_message=self._build_unsubscribe_frames,
            on_message=self._handle_message,
            heartbeat_message=lambda: "ping",
            heartbeat_interval=PING_INTERVAL,
            exchange_label=self.name,
        )

    async def get_markets(self) -> list[ExchangeMarket]:
        resp = await self._http.get("/api/v2/spot/public/symbols")
        resp.raise_for_status()
        data = resp.json()

        markets: list[ExchangeMarket] = []
        for item in data.get("data", []):
            symbol = item.get("symbol")
            base = item.get("baseCoin")
            quote = item.get("quoteCoin")
            if not symbol or not base or not quote:
                continue

            price_precision = int(item.get("pricePrecision") or 8)
            quantity_precision = int(item.get("quantityPrecision") or 8)
            status = MarketStatus.TRADING if item.get("status") == "online" else MarketStatus.HALTED

            try:
                self._fees[symbol] = (
                    float(item.get("makerFeeRate") or DEFAULT_MAKER_FEE),
                    float(item.get("takerFeeRate") or DEFAULT_TAKER_FEE),
                )
            except ValueError:
                pass

            markets.append(
                ExchangeMarket(
                    exchange=self.name,
                    symbol=symbol,
                    base_asset=base.upper(),
                    quote_asset=quote.upper(),
                    market_type="spot",
                    status=status,
                    price_precision=price_precision,
                    quantity_precision=quantity_precision,
                    tick_size=10 ** -price_precision,
                    step_size=10 ** -quantity_precision,
                    min_quantity=0.0,
                    min_notional=0.0,
                )
            )

        self._markets_count = len(markets)
        return markets

    async def get_fees(self, symbol: Optional[str] = None) -> ExchangeFees:
        maker, taker = self._fees.get(symbol or "", (DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE))
        return ExchangeFees(exchange=self.name, symbol=symbol, maker_fee=maker, taker_fee=taker)

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

    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        book = self._latest_books.get(symbol)
        if book is None:
            raise KeyError(f"No order book for {symbol} on Bitget yet (no WebSocket data)")
        return book

    async def get_ticker(self, symbol: str) -> Ticker:
        book = self._latest_books.get(symbol)
        if book is None or not book.bids or not book.asks:
            raise KeyError(f"No ticker for {symbol} on Bitget yet (no WebSocket data)")
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=book.bids[0].price,
            bid_volume=book.bids[0].quantity,
            ask=book.asks[0].price,
            ask_volume=book.asks[0].quantity,
            timestamp=book.timestamp,
        )

    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        self._orderbook_subscribers.setdefault(symbol, []).append(callback)
        self._stream.add_symbol(symbol)

    async def unsubscribe_orderbook(self, symbol: str) -> None:
        self._orderbook_subscribers.pop(symbol, None)
        self._latest_books.pop(symbol, None)
        self._stream.remove_symbol(symbol)

    async def connect(self) -> None:
        try:
            resp = await self._http.get("/api/v2/spot/public/symbols", timeout=10.0)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeUnsupportedError(f"Bitget REST reachability check failed: {exc}") from exc
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        await self._stream.stop()
        await self._http.aclose()

    @staticmethod
    def _frames(op: str, symbols: list[str]) -> list[str]:
        args = [{"instType": "SPOT", "channel": "books5", "instId": s} for s in symbols]
        return [json.dumps({"op": op, "args": chunk}) for chunk in chunked(args, MAX_ARGS_PER_FRAME)]

    def _build_subscribe_frames(self, symbols: list[str]) -> list[str]:
        return self._frames("subscribe", symbols)

    def _build_unsubscribe_frames(self, symbols: list[str]) -> list[str]:
        return self._frames("unsubscribe", symbols)

    def _handle_message(self, raw: str) -> None:
        if raw == "pong":
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        event = data.get("event")
        if event == "error":
            logger.warning("bitget: WebSocket error frame: %s", raw[:300])
            return
        if event is not None:
            return

        arg = data.get("arg", {})
        if arg.get("channel") != "books5":
            return
        symbol = arg.get("instId")
        items = data.get("data", [])
        if not symbol or not items:
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