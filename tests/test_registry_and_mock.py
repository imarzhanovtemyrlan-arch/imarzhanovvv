import pytest

from app.exchanges.base import ExchangeUnsupportedError
from app.exchanges.registry import ExchangeRegistry
from app.exchanges.adapters.mock import MockExchangeAdapter
from app.models.market import ExchangeConnectionStatus


@pytest.mark.asyncio
async def test_mock_adapter_orderbook_roundtrip(mock_adapter):
    mock_adapter.set_orderbook("BTCUSDT", bids=[(100, 1)], asks=[(101, 1)])
    book = await mock_adapter.get_orderbook("BTCUSDT")
    assert book.best_bid().price == 100
    assert book.best_ask().price == 101


@pytest.mark.asyncio
async def test_mock_adapter_subscribe_receives_updates(mock_adapter):
    received = []

    await mock_adapter.subscribe_orderbook("BTCUSDT", received.append)
    mock_adapter.set_orderbook("BTCUSDT", bids=[(100, 1)], asks=[(101, 1)])
    mock_adapter.set_orderbook("BTCUSDT", bids=[(102, 1)], asks=[(103, 1)])

    assert len(received) == 2
    assert received[-1].best_bid().price == 102


@pytest.mark.asyncio
async def test_mock_adapter_connect_status(mock_adapter):
    status = await mock_adapter.get_exchange_status()
    assert status.status == ExchangeConnectionStatus.DISCONNECTED
    await mock_adapter.connect()
    status = await mock_adapter.get_exchange_status()
    assert status.status == ExchangeConnectionStatus.CONNECTED


@pytest.mark.asyncio
async def test_registry_register_and_get():
    registry = ExchangeRegistry()
    registry.register("mock", lambda: MockExchangeAdapter(name="mock"))
    adapter = registry.get("mock")
    assert adapter.name == "mock"
    # Same instance returned on repeated get()
    assert registry.get("mock") is adapter


@pytest.mark.asyncio
async def test_registry_duplicate_registration_rejected():
    registry = ExchangeRegistry()
    registry.register("mock", lambda: MockExchangeAdapter())
    with pytest.raises(ValueError):
        registry.register("mock", lambda: MockExchangeAdapter())


@pytest.mark.asyncio
async def test_registry_marks_unsupported_on_connect_failure():
    class BrokenFactory:
        def __call__(self):
            adapter = MockExchangeAdapter(name="broken")

            async def raise_unsupported():
                raise ExchangeUnsupportedError("no public WS API available")

            adapter.connect = raise_unsupported  # type: ignore[method-assign]
            return adapter

    registry = ExchangeRegistry()
    registry.register("broken", BrokenFactory())
    await registry.connect_all()

    health = await registry.health_snapshot()
    broken_health = next(h for h in health if h.exchange == "broken")
    assert broken_health.status == ExchangeConnectionStatus.UNSUPPORTED
    assert broken_health.reason is not None
