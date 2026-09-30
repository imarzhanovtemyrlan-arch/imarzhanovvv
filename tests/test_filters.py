import time

from app.arbitrage.filters import (
    FilterConfig,
    check_available_volume,
    check_market_pair,
    check_orderbook_freshness,
    check_plausible_spread,
)
from app.models.market import MarketStatus, OrderBook, OrderBookLevel


def make_market(**overrides):
    from app.models.market import ExchangeMarket

    defaults = dict(
        exchange="mock",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        market_type="spot",
        status=MarketStatus.TRADING,
        price_precision=2,
        quantity_precision=6,
        tick_size=0.01,
        step_size=0.000001,
        min_quantity=0.0001,
        min_notional=10.0,
    )
    defaults.update(overrides)
    return ExchangeMarket(**defaults)


def test_rejects_different_base_assets():
    buy = make_market(base_asset="BTC")
    sell = make_market(base_asset="ETH")
    result = check_market_pair(buy, sell)
    assert result.passed is False
    assert "base" in result.reason


def test_rejects_different_quote_currencies():
    buy = make_market(quote_asset="USDT")
    sell = make_market(quote_asset="USDC")
    result = check_market_pair(buy, sell)
    assert result.passed is False
    assert "quote" in result.reason


def test_rejects_halted_market():
    buy = make_market(status=MarketStatus.HALTED)
    sell = make_market()
    result = check_market_pair(buy, sell)
    assert result.passed is False


def test_accepts_valid_pair():
    buy = make_market()
    sell = make_market()
    result = check_market_pair(buy, sell)
    assert result.passed is True


def test_stale_orderbook_rejected():
    old_time = time.time() - 100
    book = OrderBook(
        exchange="mock", symbol="BTCUSDT", timestamp=old_time,
        bids=[OrderBookLevel(100, 1)], asks=[OrderBookLevel(101, 1)],
        received_at=old_time,
    )
    config = FilterConfig(max_data_age_seconds=3.0)
    result = check_orderbook_freshness(book, config)
    assert result.passed is False
    assert "stale" in result.reason


def test_fresh_orderbook_accepted():
    now = time.time()
    book = OrderBook(
        exchange="mock", symbol="BTCUSDT", timestamp=now,
        bids=[OrderBookLevel(100, 1)], asks=[OrderBookLevel(101, 1)],
        received_at=now,
    )
    config = FilterConfig(max_data_age_seconds=3.0)
    result = check_orderbook_freshness(book, config)
    assert result.passed is True


def test_empty_side_rejected():
    now = time.time()
    book = OrderBook(exchange="mock", symbol="BTCUSDT", timestamp=now, bids=[], asks=[], received_at=now)
    result = check_orderbook_freshness(book, FilterConfig())
    assert result.passed is False


def test_low_volume_rejected():
    config = FilterConfig(min_available_notional=500.0)
    result = check_available_volume(100.0, config)
    assert result.passed is False


def test_implausible_spread_rejected():
    config = FilterConfig(max_plausible_gross_spread=0.15)
    result = check_plausible_spread(0.5, config)  # 50% spread - almost certainly bad data
    assert result.passed is False


def test_plausible_spread_accepted():
    config = FilterConfig(max_plausible_gross_spread=0.15)
    result = check_plausible_spread(0.003, config)
    assert result.passed is True
