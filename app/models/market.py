"""
Unified domain models used across the whole system.

Every exchange adapter MUST normalize its raw data into these dataclasses.
The arbitrage engine and API layer NEVER depend on exchange-specific formats.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MarketStatus(str, Enum):
    TRADING = "trading"
    HALTED = "halted"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


class ExchangeConnectionStatus(str, Enum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    UNSUPPORTED = "unsupported"  # adapter exists but exchange API can't be reliably integrated


@dataclass(frozen=True)
class ExchangeMarket:
    """Normalized description of a single tradable spot market on one exchange."""

    exchange: str
    symbol: str  # original symbol as used by the exchange, e.g. "BTCUSDT"
    base_asset: str  # normalized, e.g. "BTC"
    quote_asset: str  # normalized, e.g. "USDT"
    market_type: str  # "spot" (only spot is in scope for MVP)
    status: MarketStatus
    price_precision: int
    quantity_precision: int
    tick_size: float
    step_size: float
    min_quantity: float
    min_notional: float

    @property
    def normalized_pair(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}"


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    quantity: float


@dataclass
class OrderBook:
    exchange: str
    symbol: str
    timestamp: float  # exchange-provided or receipt time, epoch seconds
    bids: list[OrderBookLevel]  # sorted descending by price
    asks: list[OrderBookLevel]  # sorted ascending by price
    sequence: Optional[int] = None
    received_at: float = field(default=0.0)  # local receipt time, for latency/staleness calc

    def best_bid(self) -> Optional[OrderBookLevel]:
        return self.bids[0] if self.bids else None

    def best_ask(self) -> Optional[OrderBookLevel]:
        return self.asks[0] if self.asks else None

    def is_stale(self, ttl_seconds: float, now: float) -> bool:
        reference = self.received_at or self.timestamp
        return (now - reference) > ttl_seconds


@dataclass(frozen=True)
class Ticker:
    exchange: str
    symbol: str
    bid: float
    bid_volume: float
    ask: float
    ask_volume: float
    timestamp: float


@dataclass(frozen=True)
class ExchangeFees:
    exchange: str
    symbol: Optional[str]  # None means "default for exchange"
    maker_fee: float  # as a fraction, e.g. 0.001 == 0.10%
    taker_fee: float


@dataclass
class ExchangeHealth:
    exchange: str
    status: ExchangeConnectionStatus
    markets_count: int
    last_update: Optional[float]
    latency_ms: Optional[float]
    reason: Optional[str] = None  # populated when DISCONNECTED / UNSUPPORTED / ERROR


class OpportunityStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    FILTERED = "filtered"  # failed validation (see arbitrage/filters.py)


@dataclass
class ArbitrageOpportunity:
    id: str
    symbol: str  # normalized "BASE/QUOTE"
    buy_exchange: str
    sell_exchange: str

    buy_price: float
    sell_price: float

    gross_spread: float  # fraction, e.g. 0.005 == 0.5%

    buy_fee: float  # fraction
    sell_fee: float  # fraction

    estimated_slippage: float  # fraction
    net_spread: float  # fraction

    available_volume: float  # in quote currency, capped by both books' depth
    recommended_trade_size: float  # in quote currency

    estimated_profit: float  # in quote currency, for recommended_trade_size

    timestamp: float
    data_age: float  # seconds, max(buy_book_age, sell_book_age)

    status: OpportunityStatus = OpportunityStatus.ACTIVE
