from app.arbitrage.vwap import max_available_notional, vwap_for_notional
from app.models.market import OrderBookLevel


def test_vwap_single_level_full_fill():
    levels = [OrderBookLevel(price=100.0, quantity=10.0)]
    result = vwap_for_notional(levels, target_notional=500.0)
    assert result.vwap_price == 100.0
    assert result.filled_base_quantity == 5.0
    assert result.filled_quote_notional == 500.0
    assert result.fully_filled is True


def test_vwap_walks_multiple_levels():
    levels = [
        OrderBookLevel(price=100.0, quantity=2.0),  # $200
        OrderBookLevel(price=101.0, quantity=2.0),  # $202
        OrderBookLevel(price=102.0, quantity=10.0),  # plenty
    ]
    result = vwap_for_notional(levels, target_notional=500.0)
    # First two levels fully consumed = $402, remaining $98 at 102
    expected_base = 2.0 + 2.0 + (98.0 / 102.0)
    assert abs(result.filled_base_quantity - expected_base) < 1e-9
    assert abs(result.filled_quote_notional - 500.0) < 1e-9
    assert result.fully_filled is True


def test_vwap_insufficient_depth():
    levels = [OrderBookLevel(price=100.0, quantity=1.0)]  # only $100 available
    result = vwap_for_notional(levels, target_notional=500.0)
    assert result.fully_filled is False
    assert result.filled_quote_notional == 100.0


def test_vwap_zero_target():
    levels = [OrderBookLevel(price=100.0, quantity=1.0)]
    result = vwap_for_notional(levels, target_notional=0.0)
    assert result.filled_base_quantity == 0.0
    assert result.fully_filled is True


def test_max_available_notional():
    levels = [
        OrderBookLevel(price=100.0, quantity=1.0),
        OrderBookLevel(price=101.0, quantity=2.0),
    ]
    assert max_available_notional(levels) == 100.0 + 202.0
