"""
Filters for rejecting false arbitrage signals (spec section 10 & 36).

These run BEFORE any profit is computed or shown to the user. A candidate
pair must pass every filter here to even be considered.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from app.models.market import ExchangeMarket, MarketStatus, OrderBook


@dataclass(frozen=True)
class FilterConfig:
    max_data_age_seconds: float = 3.0
    min_available_notional: float = 10.0  # quote currency
    # A spread this large almost certainly indicates bad/stale data rather
    # than a real opportunity - flag for confirmation instead of showing it.
    max_plausible_gross_spread: float = 0.15  # 15%


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    reason: Optional[str] = None


def check_market_pair(
    buy_market: ExchangeMarket,
    sell_market: ExchangeMarket,
) -> FilterResult:
    if buy_market.base_asset != sell_market.base_asset:
        return FilterResult(False, "different base assets")
    if buy_market.quote_asset != sell_market.quote_asset:
        return FilterResult(False, "different quote currencies")
    if buy_market.status != MarketStatus.TRADING:
        return FilterResult(False, f"buy market status is {buy_market.status.value}")
    if sell_market.status != MarketStatus.TRADING:
        return FilterResult(False, f"sell market status is {sell_market.status.value}")
    return FilterResult(True)


def check_orderbook_freshness(
    book: OrderBook, config: FilterConfig, now: Optional[float] = None
) -> FilterResult:
    now = now if now is not None else time.time()
    if book.is_stale(config.max_data_age_seconds, now):
        age = now - (book.received_at or book.timestamp)
        return FilterResult(False, f"stale order book ({age:.1f}s old)")
    if not book.bids or not book.asks:
        return FilterResult(False, "empty order book side")
    return FilterResult(True)


def check_available_volume(available_notional: float, config: FilterConfig) -> FilterResult:
    if available_notional < config.min_available_notional:
        return FilterResult(
            False, f"available volume ${available_notional:.2f} below minimum ${config.min_available_notional:.2f}"
        )
    return FilterResult(True)


def check_plausible_spread(gross_spread: float, config: FilterConfig) -> FilterResult:
    """A gross spread above this threshold is far more likely to be a data
    error (wrong precision, halted market, bad tick) than a real opportunity.
    We don't show it until it can be confirmed via a fresh REST snapshot."""
    if gross_spread > config.max_plausible_gross_spread:
        return FilterResult(
            False, f"gross spread {gross_spread:.2%} exceeds plausibility threshold, needs confirmation"
        )
    return FilterResult(True)


def check_precision_sane(market: ExchangeMarket) -> FilterResult:
    if market.tick_size <= 0 or market.step_size <= 0:
        return FilterResult(False, f"[{market.exchange}] invalid tick/step size for {market.symbol}")
    if market.min_quantity < 0 or market.min_notional < 0:
        return FilterResult(False, f"[{market.exchange}] invalid min quantity/notional for {market.symbol}")
    return FilterResult(True)
