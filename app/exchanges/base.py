"""
ExchangeAdapter: the universal contract every exchange integration must implement.

Design goals (see project spec):
- Core (arbitrage engine, API) never depends on exchange-specific formats.
- New exchanges are added by subclassing this and registering in ExchangeRegistry.
- Adapters that cannot reliably implement an exchange's API must raise
  ExchangeUnsupportedError instead of faking data.
"""
from __future__ import annotations

import abc
import logging
from typing import AsyncIterator, Callable, Optional

from app.models.market import (
    ExchangeFees,
    ExchangeHealth,
    ExchangeMarket,
    OrderBook,
    Ticker,
)

logger = logging.getLogger(__name__)


class ExchangeAdapterError(Exception):
    """Base class for all adapter errors."""


class ExchangeUnsupportedError(ExchangeAdapterError):
    """Raised when a feature or the whole exchange cannot be reliably supported.

    The registry catches this at startup/connect time and marks the exchange
    UNSUPPORTED instead of crashing the rest of the system.
    """


class ExchangeRateLimitError(ExchangeAdapterError):
    """Raised when the exchange rejects a request due to rate limiting."""


OrderBookCallback = Callable[[OrderBook], None]


class ExchangeAdapter(abc.ABC):
    """Base class for all exchange integrations.

    Subclasses represent ONE exchange and are responsible for:
      - talking to that exchange's actual REST/WebSocket API
      - normalizing all responses into the shared models in app.models.market
      - managing their own connection lifecycle (reconnect, heartbeat, backoff)

    Nothing outside this class should ever see exchange-native payloads.
    """

    #: Short unique lowercase id, e.g. "binance". Must match registry key.
    name: str = "base"

    #: Set True only once a maintainer has verified the adapter against the
    #: exchange's real docs and it is safe to enable in production.
    is_supported: bool = False

    def __init__(self) -> None:
        self._orderbook_subscribers: dict[str, list[OrderBookCallback]] = {}
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def get_markets(self) -> list[ExchangeMarket]:
        """Return all spot markets currently tradable on this exchange."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_fees(self, symbol: Optional[str] = None) -> ExchangeFees:
        """Return maker/taker fees, optionally for a specific symbol."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_exchange_status(self) -> ExchangeHealth:
        """Return current connection/health info for this exchange."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Market data - REST
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 50) -> OrderBook:
        """REST snapshot of the order book. Used for initial snapshot / recovery / fallback."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Market data - WebSocket (primary source per spec section 5)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def subscribe_orderbook(self, symbol: str, callback: OrderBookCallback) -> None:
        """Subscribe to live order book updates for `symbol`.

        Implementations must handle reconnect/backoff/heartbeat internally
        and keep calling `callback` with normalized OrderBook snapshots/deltas
        merged into a full book.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def unsubscribe_orderbook(self, symbol: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish WebSocket connection(s). Must be idempotent."""
        raise NotImplementedError

    @abc.abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        return self._connected
