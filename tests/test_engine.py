import time

from app.arbitrage.engine import ArbitrageEngine, ExchangeSnapshot
from app.arbitrage.filters import FilterConfig
from app.models.market import ExchangeFees, ExchangeMarket, MarketStatus, OrderBook, OrderBookLevel


def make_market(exchange: str, **overrides) -> ExchangeMarket:
    defaults = dict(
        exchange=exchange,
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


def make_book(exchange: str, bids, asks, timestamp=None) -> OrderBook:
    now = timestamp if timestamp is not None else time.time()
    return OrderBook(
        exchange=exchange, symbol="BTCUSDT", timestamp=now,
        bids=[OrderBookLevel(p, q) for p, q in bids],
        asks=[OrderBookLevel(p, q) for p, q in asks],
        received_at=now,
    )


def make_snapshot(exchange: str, bids, asks, fee=0.001, market_overrides=None) -> ExchangeSnapshot:
    return ExchangeSnapshot(
        exchange=exchange,
        market=make_market(exchange, **(market_overrides or {})),
        order_book=make_book(exchange, bids, asks),
        fees=ExchangeFees(exchange=exchange, symbol="BTCUSDT", maker_fee=fee, taker_fee=fee),
    )


def test_finds_profitable_opportunity():
    engine = ArbitrageEngine()
    snapshots = [
        make_snapshot("okx", bids=[(99900, 5)], asks=[(100000, 5)]),
        make_snapshot("mexc", bids=[(100600, 5)], asks=[(100700, 5)]),
    ]
    opportunities = engine.find_opportunities("BTC/USDT", snapshots, trade_size_quote=1000)
    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp.buy_exchange == "okx"
    assert opp.sell_exchange == "mexc"
    assert opp.net_spread > 0
    assert opp.estimated_profit > 0


def test_no_opportunity_when_prices_equal():
    engine = ArbitrageEngine()
    snapshots = [
        make_snapshot("a", bids=[(100000, 5)], asks=[(100010, 5)]),
        make_snapshot("b", bids=[(100000, 5)], asks=[(100010, 5)]),
    ]
    opportunities = engine.find_opportunities("BTC/USDT", snapshots)
    assert opportunities == []


def test_rejects_stale_book():
    engine = ArbitrageEngine(filter_config=FilterConfig(max_data_age_seconds=1.0))
    stale_time = time.time() - 30
    snapshots = [
        make_snapshot("a", bids=[(99900, 5)], asks=[(100000, 5)]),
        ExchangeSnapshot(
            exchange="b",
            market=make_market("b"),
            order_book=make_book("b", bids=[(100600, 5)], asks=[(100700, 5)], timestamp=stale_time),
            fees=ExchangeFees(exchange="b", symbol="BTCUSDT", maker_fee=0.001, taker_fee=0.001),
        ),
    ]
    opportunities = engine.find_opportunities("BTC/USDT", snapshots)
    assert opportunities == []


def test_rejects_different_quote_currency():
    engine = ArbitrageEngine()
    snapshots = [
        make_snapshot("a", bids=[(99900, 5)], asks=[(100000, 5)], market_overrides={"quote_asset": "USDT"}),
        make_snapshot("b", bids=[(100600, 5)], asks=[(100700, 5)], market_overrides={"quote_asset": "USDC"}),
    ]
    opportunities = engine.find_opportunities("BTC/USDT", snapshots)
    assert opportunities == []


def test_high_fees_eliminate_opportunity():
    engine = ArbitrageEngine()
    snapshots = [
        make_snapshot("a", bids=[(99990, 5)], asks=[(100000, 5)], fee=0.01),
        make_snapshot("b", bids=[(100010, 5)], asks=[(100020, 5)], fee=0.01),
    ]
    opportunities = engine.find_opportunities("BTC/USDT", snapshots)
    assert opportunities == []


def test_implausible_spread_excluded():
    engine = ArbitrageEngine()
    snapshots = [
        make_snapshot("a", bids=[(100, 5)], asks=[(100, 5)]),
        make_snapshot("b", bids=[(1000, 5)], asks=[(1000, 5)]),  # 10x spread - clearly bad data
    ]
    opportunities = engine.find_opportunities("BTC/USDT", snapshots)
    assert opportunities == []


def test_results_sorted_by_net_spread_desc():
    engine = ArbitrageEngine()
    snapshots = [
        make_snapshot("a", bids=[(99900, 5)], asks=[(100000, 5)]),
        make_snapshot("b", bids=[(100600, 5)], asks=[(100700, 5)]),
        make_snapshot("c", bids=[(101200, 5)], asks=[(101300, 5)]),
    ]
    opportunities = engine.find_opportunities("BTC/USDT", snapshots, trade_size_quote=1000)
    spreads = [o.net_spread for o in opportunities]
    assert spreads == sorted(spreads, reverse=True)


def test_opportunity_id_is_stable_and_data_age_uses_oldest_book():
    import time
    from app.models.market import ExchangeFees, ExchangeMarket, MarketStatus, OrderBook, OrderBookLevel
    from app.arbitrage.engine import ExchangeSnapshot

    now = time.time()
    market_a = ExchangeMarket("a", "BTCUSDT", "BTC", "USDT", "spot", MarketStatus.TRADING, 2, 6, 0.01, 0.000001, 0.0, 1.0)
    market_b = ExchangeMarket("b", "BTCUSDT", "BTC", "USDT", "spot", MarketStatus.TRADING, 2, 6, 0.01, 0.000001, 0.0, 1.0)
    old = OrderBook("a", "BTCUSDT", now - 2.0, [OrderBookLevel(99900, 10)], [OrderBookLevel(100000, 10)], now - 2.0)
    fresh = OrderBook("b", "BTCUSDT", now - 0.2, [OrderBookLevel(100500, 10)], [OrderBookLevel(100600, 10)], now - 0.2)
    fees_a = ExchangeFees("a", "BTCUSDT", 0.0, 0.0)
    fees_b = ExchangeFees("b", "BTCUSDT", 0.0, 0.0)
    engine = ArbitrageEngine(filter_config=FilterConfig(max_data_age_seconds=3.0))
    opps = engine.find_opportunities("BTC/USDT", [
        ExchangeSnapshot("a", market_a, old, fees_a),
        ExchangeSnapshot("b", market_b, fresh, fees_b),
    ], now=now)
    assert opps
    assert opps[0].id == "BTC/USDT:a:b"
    assert abs(opps[0].data_age - 2.0) < 0.05
