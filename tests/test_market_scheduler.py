from app.market_data.scheduler import MarketScheduler
from app.models.market import ExchangeMarket, MarketStatus


def market(exchange: str, base: str, quote: str) -> ExchangeMarket:
    return ExchangeMarket(
        exchange=exchange, symbol=f"{base}{quote}", base_asset=base, quote_asset=quote,
        market_type="spot", status=MarketStatus.TRADING, price_precision=2, quantity_precision=6,
        tick_size=0.01, step_size=0.000001, min_quantity=0.0001, min_notional=1.0,
    )


def test_universe_is_not_truncated_by_live_capacity():
    markets = {
        "BTC/USDT": {"a": market("a", "BTC", "USDT"), "b": market("b", "BTC", "USDT")},
        "ETH/USDT": {"a": market("a", "ETH", "USDT"), "b": market("b", "ETH", "USDT")},
        "DOGE/USDT": {"a": market("a", "DOGE", "USDT"), "b": market("b", "DOGE", "USDT")},
    }
    plan, stats = MarketScheduler(max_live_pairs_per_exchange=2).build_plan(markets)
    assert stats.universe_pairs == 3
    assert stats.candidate_pairs == 3
    assert stats.live_subscriptions == 4
    assert stats.deferred_pairs == 1
    assert {x.pair for x in plan} == {"BTC/USDT", "DOGE/USDT"}


def test_pair_is_atomic_across_its_exchanges():
    markets = {
        "BTC/USDT": {"a": market("a", "BTC", "USDT"), "b": market("b", "BTC", "USDT"), "c": market("c", "BTC", "USDT")},
        "ETH/EUR": {"a": market("a", "ETH", "EUR"), "b": market("b", "ETH", "EUR")},
    }
    plan, stats = MarketScheduler(max_live_pairs_per_exchange=1).build_plan(markets)
    assert {x.pair for x in plan} == {"BTC/USDT"}
    assert stats.deferred_pairs == 1
    assert stats.live_pairs_by_exchange == {"a": 1, "b": 1, "c": 1}


def test_none_means_all_live_subscriptions():
    markets = {
        f"A{i}/USDT": {"a": market("a", f"A{i}", "USDT"), "b": market("b", f"A{i}", "USDT")}
        for i in range(25)
    }
    plan, stats = MarketScheduler(max_live_pairs_per_exchange=None).build_plan(markets)
    assert stats.live_subscriptions == 50
    assert stats.deferred_pairs == 0
    assert len(plan) == 50
