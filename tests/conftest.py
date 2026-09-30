import pytest

from app.exchanges.adapters.mock import MockExchangeAdapter
from app.models.market import ExchangeMarket, MarketStatus


@pytest.fixture
def mock_market() -> ExchangeMarket:
    return ExchangeMarket(
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


@pytest.fixture
def mock_adapter() -> MockExchangeAdapter:
    return MockExchangeAdapter(name="mock")
