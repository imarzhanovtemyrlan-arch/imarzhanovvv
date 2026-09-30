"""
MockExchangeAdapter: a fully in-memory, deterministic adapter used by:
  - unit tests (arbitrage engine, VWAP, staleness, etc.) - see spec section 25
  - local development when real exchange connectivity is unavailable

It implements the full ExchangeAdapter contract without any network I/O.
Order books are set programmatically via `set_orderbook(...)`.
"""
from __future__ import annotations

import time
from typing import Optional

from app.exchanges.base import ExchangeAdapter, OrderBookCallback
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


class MockExchangeAdapter(ExchangeAdapter):
    is_supported = True

    def __init__(self, name: str = "mock", maker_fee: float = 0.001, taker_fee: float = 0.001) -> None:
        super().__init__()
        self.name = name
        self._maker_fee = maker_fee
        self._taker_fee = taker_fee
        self._books: dict[str, OrderBook] = {}
        self._markets: dict[str, ExchangeMarket] = {}

    # -- test helpers -----------------------------------------------------

    def add_market(self, market: ExchangeMarket) -> None:
        self._markets[market.symbol] = market

    def set_orderbook(
        self,
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        timestamp: Optional[float] = None,
    ) -> None:
        now = timestamp if timestamp is not None else time.time()
        book = OrderBook(
            exchange=self.name,
            symbol=symbol,
            timestamp=now,
            bids=[OrderBookLevel(p, q) for p, q in sorted(bids, key=lambda x: -x[0])],
            asks=[OrderBookLevel(p, q) for p, q in sorted(asks, key=lambda x: x[0])],
            received_at=now,
        )
        self._books[symbol] = book
        for cb in self._orderbook_subscribers.get(symbol, []):
            cb(book)

    # -- ExchangeAdapter interface -----------------------------------------

    async def get_markets(self) -> list[ExchangeMarket]:
        return list(self._markets.values())

    async def get_fees(self, symbol: Optional[str] = None) -> ExchangeFees:
        return ExchangeFees(
            exchange=self.name, symbol=symbol, maker_fee=self._maker_fee, taker_fee=self._taker_fee
        )

    async def get_exchange_status(self) -> ExchangeHealth:
        return ExchangeHealth(
            exchange=self.name,
            status=ExchangeConnectionStatus.CONNECTED if self._connected else ExchangeConnectionStatus.DISCONNECTED,
            markets_count=len(self._markets),
            last_update=time.time(),
            latency_ms=1.0,
        )

    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        if symbol not in self._books:
            raise KeyError(f"No mock order book set for {symbol}")
        return self._books[symbol]

    async def get_ticker(self, symbol: str) -> Ticker:
        book = await self.get_orderbook(symbol)
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=best_bid.price if best_bid else 0.0,
            bid_volume=best_bid.quantity if best_bid else 0.0,
            ask=best_ask.price if best_ask else 0.0,
            ask_volume=best_ask.quantity if best_ask else 0.0,
            timestamp=book.timestamp,
        )

    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        self._orderbook_subscribers.setdefault(symbol, []).append(callback)
        if symbol in self._books:
            callback(self._books[symbol])

    async def unsubscribe_orderbook(self, symbol: str) -> None:
        self._orderbook_subscribers.pop(symbol, None)

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
