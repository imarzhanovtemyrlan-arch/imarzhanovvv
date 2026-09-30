"""
ArbitrageEngine: the module described in spec section 8.

Given current order books for a normalized symbol across multiple exchanges,
finds BUY-low/SELL-high pairs, runs them through the false-signal filters
(section 10), computes real VWAP-based profit (section 9), and emits
ArbitrageOpportunity objects.

This module has ZERO knowledge of any specific exchange's API - it only
consumes the normalized OrderBook / ExchangeMarket / ExchangeFees models.
"""
from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass

from app.arbitrage.calculator import compute_profit
from app.arbitrage.filters import (
    FilterConfig,
    check_available_volume,
    check_market_pair,
    check_orderbook_freshness,
    check_plausible_spread,
)
from app.arbitrage.vwap import max_available_notional
from app.models.market import (
    ArbitrageOpportunity,
    ExchangeFees,
    ExchangeMarket,
    OpportunityStatus,
    OrderBook,
)

logger = logging.getLogger(__name__)

DEFAULT_TRADE_SIZE_QUOTE = 1000.0  # default $1000 notional for the headline opportunity


@dataclass(frozen=True)
class ExchangeSnapshot:
    """Everything the engine needs about one exchange for one symbol."""

    exchange: str
    market: ExchangeMarket
    order_book: OrderBook
    fees: ExchangeFees


class ArbitrageEngine:
    def __init__(self, filter_config: FilterConfig | None = None) -> None:
        self.filter_config = filter_config or FilterConfig()

    def find_opportunities(
        self,
        symbol: str,
        snapshots: list[ExchangeSnapshot],
        trade_size_quote: float = DEFAULT_TRADE_SIZE_QUOTE,
        now: float | None = None,
    ) -> list[ArbitrageOpportunity]:
        """Compare every exchange pair for `symbol` and return valid opportunities.

        `snapshots` must all refer to the SAME normalized symbol (already
        matched via SymbolNormalizer upstream) but can come from any number
        of exchanges - no hardcoded exchange list (spec section 4).
        """
        now = now if now is not None else time.time()
        opportunities: list[ArbitrageOpportunity] = []

        for buy_snap, sell_snap in itertools.permutations(snapshots, 2):
            if buy_snap.exchange == sell_snap.exchange:
                continue

            pair_check = check_market_pair(buy_snap.market, sell_snap.market)
            if not pair_check.passed:
                logger.debug("Rejected pair %s/%s: %s", buy_snap.exchange, sell_snap.exchange, pair_check.reason)
                continue

            buy_fresh = check_orderbook_freshness(buy_snap.order_book, self.filter_config, now)
            sell_fresh = check_orderbook_freshness(sell_snap.order_book, self.filter_config, now)
            if not buy_fresh.passed or not sell_fresh.passed:
                logger.debug(
                    "Rejected pair %s/%s: %s",
                    buy_snap.exchange,
                    sell_snap.exchange,
                    buy_fresh.reason or sell_fresh.reason,
                )
                continue

            best_ask = buy_snap.order_book.best_ask()
            best_bid = sell_snap.order_book.best_bid()
            if best_ask is None or best_bid is None or best_ask.price <= 0:
                continue

            naive_gross_spread = (best_bid.price - best_ask.price) / best_ask.price
            if naive_gross_spread <= 0:
                continue  # not even a naive opportunity, skip silently

            plausible = check_plausible_spread(naive_gross_spread, self.filter_config)
            if not plausible.passed:
                logger.warning(
                    "Suspicious spread %s->%s on %s: %s",
                    buy_snap.exchange, sell_snap.exchange, symbol, plausible.reason,
                )
                continue

            available_notional = min(
                max_available_notional(buy_snap.order_book.asks),
                max_available_notional(sell_snap.order_book.bids),
            )
            volume_check = check_available_volume(available_notional, self.filter_config)
            if not volume_check.passed:
                continue

            actual_trade_size = min(trade_size_quote, available_notional)
            breakdown = compute_profit(
                buy_book=buy_snap.order_book,
                sell_book=sell_snap.order_book,
                trade_size_quote=actual_trade_size,
                buy_taker_fee=buy_snap.fees.taker_fee,
                sell_taker_fee=sell_snap.fees.taker_fee,
            )

            if breakdown.net_spread <= 0:
                continue  # not profitable after fees/slippage - don't surface it

            # Both legs must be fresh; the pair age is therefore the age of
            # the older (minimum timestamp) book.
            data_age = now - min(
                buy_snap.order_book.received_at or buy_snap.order_book.timestamp,
                sell_snap.order_book.received_at or sell_snap.order_book.timestamp,
            )

            opportunities.append(
                ArbitrageOpportunity(
                    id=f"{symbol}:{buy_snap.exchange}:{sell_snap.exchange}",
                    symbol=symbol,
                    buy_exchange=buy_snap.exchange,
                    sell_exchange=sell_snap.exchange,
                    buy_price=breakdown.buy_vwap,
                    sell_price=breakdown.sell_vwap,
                    gross_spread=breakdown.gross_spread,
                    buy_fee=breakdown.buy_fee,
                    sell_fee=breakdown.sell_fee,
                    estimated_slippage=breakdown.estimated_slippage,
                    net_spread=breakdown.net_spread,
                    available_volume=available_notional,
                    recommended_trade_size=actual_trade_size,
                    estimated_profit=breakdown.estimated_profit,
                    timestamp=now,
                    data_age=data_age,
                    status=OpportunityStatus.ACTIVE,
                )
            )

        opportunities.sort(key=lambda o: o.net_spread, reverse=True)
        return opportunities
