"""
VWAP calculation across real order book depth.

Per spec section 9: profit must NOT be computed from best bid/ask alone.
We walk the book level by level until the requested notional (quote-currency)
size is filled, or the book runs out of depth.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.market import OrderBookLevel


@dataclass(frozen=True)
class VWAPResult:
    vwap_price: float
    filled_base_quantity: float
    filled_quote_notional: float
    fully_filled: bool  # False if the book didn't have enough depth


def vwap_for_notional(levels: list[OrderBookLevel], target_notional: float) -> VWAPResult:
    """Walk `levels` (asks for a buy, bids for a sell) accumulating quote notional
    until `target_notional` is reached.

    Returns the volume-weighted average price actually achievable, and whether
    the full target size could be filled given available depth.
    """
    if target_notional <= 0:
        return VWAPResult(0.0, 0.0, 0.0, True)

    remaining_notional = target_notional
    filled_base = 0.0
    filled_quote = 0.0

    for level in levels:
        level_notional = level.price * level.quantity
        if level_notional <= remaining_notional:
            filled_base += level.quantity
            filled_quote += level_notional
            remaining_notional -= level_notional
        else:
            # Partial fill of this level.
            partial_qty = remaining_notional / level.price
            filled_base += partial_qty
            filled_quote += remaining_notional
            remaining_notional = 0.0
            break

    fully_filled = remaining_notional <= 1e-9
    vwap_price = (filled_quote / filled_base) if filled_base > 0 else 0.0
    return VWAPResult(
        vwap_price=vwap_price,
        filled_base_quantity=filled_base,
        filled_quote_notional=filled_quote,
        fully_filled=fully_filled,
    )


def max_available_notional(levels: list[OrderBookLevel], max_levels: int = 100) -> float:
    """Total quote notional available across the top `max_levels` of the book."""
    return sum(level.price * level.quantity for level in levels[:max_levels])
