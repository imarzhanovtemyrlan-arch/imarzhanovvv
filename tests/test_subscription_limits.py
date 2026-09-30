"""Tests for subscribe chunking and the tracked-pair cap."""
import json

from app.exchanges.adapters._ws_common import chunked
from app.exchanges.adapters.bybit import BybitAdapter, MAX_ARGS_PER_SUBSCRIBE
from app.exchanges.adapters.mock import MockExchangeAdapter
from app.exchanges.registry import ExchangeRegistry
from app.market_data.manager import MarketDataManager
from app.models.market import ExchangeMarket, MarketStatus


def test_chunked_splits_evenly_and_keeps_remainder():
    assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunked([], 3) == []


def test_bybit_subscribe_is_chunked():
    adapter = BybitAdapter()
    symbols = [f"S{i}USDT" for i in range(25)]
    frames = adapter._build_subscribe_frames(symbols)
    assert len(frames) == 3  # 10 + 10 + 5
    sent = [a for f in frames for a in json.loads(f)["args"]]
    assert len(sent) == 25
    assert all(len(json.loads(f)["args"]) <= MAX_ARGS_PER_SUBSCRIBE for f in frames)


def _market(exchange: str, base: str, quote: str = "USDT") -> ExchangeMarket:
    return ExchangeMarket(
        exchange=exchange, symbol=f"{base}{quote}", base_asset=base, quote_asset=quote,
        market_type="spot", status=MarketStatus.TRADING, price_precision=2, quantity_precision=6,
        tick_size=0.01, step_size=0.000001, min_quantity=0.0001, min_notional=1.0,
    )


async def _manager(**kwargs) -> MarketDataManager:
    registry = ExchangeRegistry()
    for name in ("a", "b", "c"):
        adapter = MockExchangeAdapter(name=name)
        registry.register(name, lambda adapter=adapter: adapter)
    # BTC on all 3 exchanges, ETH on 2, TINY on 2 (different quote), XRP on 1 only
    registry.get("a").add_market(_market("a", "BTC"))
    registry.get("b").add_market(_market("b", "BTC"))
    registry.get("c").add_market(_market("c", "BTC"))
    registry.get("a").add_market(_market("a", "ETH"))
    registry.get("b").add_market(_market("b", "ETH"))
    registry.get("a").add_market(_market("a", "TINY", "EUR"))
    registry.get("b").add_market(_market("b", "TINY", "EUR"))
    registry.get("a").add_market(_market("a", "XRP"))
    manager = MarketDataManager(registry, **kwargs)
    await manager.discover_markets()
    return manager


async def test_pairs_prioritized_by_exchange_count_and_capped():
    manager = await _manager(max_tracked_pairs=1)
    assert manager.tradable_pairs() == ["BTC/USDT"]  # listed on 3 exchanges beats 2


async def test_quote_asset_filter_excludes_untracked_quotes():
    manager = await _manager(tracked_quote_assets={"USDT"})
    pairs = manager.tradable_pairs()
    assert "TINY/EUR" not in pairs
    assert set(pairs) == {"BTC/USDT", "ETH/USDT"}  # XRP only on one exchange


async def test_no_cap_by_default():
    manager = await _manager()
    assert set(manager.tradable_pairs()) == {"BTC/USDT", "ETH/USDT", "TINY/EUR"}


async def test_all_quote_assets_are_tracked_by_default():
    manager = await _manager()
    assert "TINY/EUR" in manager.tradable_pairs()
