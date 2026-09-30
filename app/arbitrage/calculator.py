"""
Real-profit calculator (spec section 9).

Computes gross spread, fees, slippage and net spread/profit for a given
target trade size, walking real order book depth via VWAP rather than
best bid/ask only.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.arbitrage.vwap import vwap_for_notional
from app.models.market import OrderBook


@dataclass(frozen=True)
class ProfitBreakdown:
    trade_size_quote: float  # requested notional in quote currency

    buy_vwap: float
    sell_vwap: float

    gross_spread: float  # (sell_vwap - buy_vwap) / buy_vwap
    buy_fee: float
    sell_fee: float
    estimated_slippage: float  # vs best bid/ask, fraction
    net_spread: float  # gross - fees - slippage

    filled_quote: float  # actual notional achievable (may be < trade_size_quote)
    fully_filled: bool

    estimated_profit: float  # in quote currency, based on filled_quote and net_spread


def compute_profit(
    buy_book: OrderBook,
    sell_book: OrderBook,
    trade_size_quote: float,
    buy_taker_fee: float,
    sell_taker_fee: float,
) -> ProfitBreakdown:
    best_ask = buy_book.best_ask()
    best_bid = sell_book.best_bid()
    if best_ask is None or best_bid is None:
        raise ValueError("both order books must have at least one level on the relevant side")

    buy_result = vwap_for_notional(buy_book.asks, trade_size_quote)
    sell_result = vwap_for_notional(sell_book.bids, trade_size_quote)

    # Use the smaller side's fill so profit reflects what can actually be
    # executed on BOTH legs (spec 9: "потенциальные дополнительные расходы").
    filled_quote = min(buy_result.filled_quote_notional, sell_result.filled_quote_notional)
    fully_filled = buy_result.fully_filled and sell_result.fully_filled

    if buy_result.vwap_price <= 0 or sell_result.vwap_price <= 0:
        gross_spread = 0.0
    else:
        gross_spread = (sell_result.vwap_price - buy_result.vwap_price) / buy_result.vwap_price

    # Slippage is already reflected in VWAP versus best ask/bid. Keep it as
    # an informational metric, but do NOT subtract it again from gross spread.
    buy_slippage = (
        (buy_result.vwap_price - best_ask.price) / best_ask.price if buy_result.vwap_price > 0 else 0.0
    )
    sell_slippage = (
        (best_bid.price - sell_result.vwap_price) / best_bid.price if sell_result.vwap_price > 0 else 0.0
    )
    estimated_slippage = max(0.0, buy_slippage) + max(0.0, sell_slippage)

    net_spread = gross_spread - buy_taker_fee - sell_taker_fee
    estimated_profit = net_spread * filled_quote

    return ProfitBreakdown(
        trade_size_quote=trade_size_quote,
        buy_vwap=buy_result.vwap_price,
        sell_vwap=sell_result.vwap_price,
        gross_spread=gross_spread,
        buy_fee=buy_taker_fee,
        sell_fee=sell_taker_fee,
        estimated_slippage=estimated_slippage,
        net_spread=net_spread,
        filled_quote=filled_quote,
        fully_filled=fully_filled,
        estimated_profit=estimated_profit,
    )
