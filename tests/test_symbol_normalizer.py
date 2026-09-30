import pytest

from app.exchanges.symbol_normalizer import SymbolNormalizationError, SymbolNormalizer


@pytest.fixture
def normalizer() -> SymbolNormalizer:
    return SymbolNormalizer()


@pytest.mark.parametrize(
    "raw,expected_base,expected_quote",
    [
        ("BTCUSDT", "BTC", "USDT"),
        ("BTC/USDT", "BTC", "USDT"),
        ("BTC-USDT", "BTC", "USDT"),
        ("BTC_USDT", "BTC", "USDT"),
        ("ETHBTC", "ETH", "BTC"),
        ("ethusdt", "ETH", "USDT"),
        ("SOLUSDC", "SOL", "USDC"),
    ],
)
def test_normalize_equivalent_pairs(normalizer, raw, expected_base, expected_quote):
    result = normalizer.normalize("binance", raw)
    assert result.base_asset == expected_base
    assert result.quote_asset == expected_quote
    assert result.pair == f"{expected_base}/{expected_quote}"


def test_normalize_preserves_original_symbol(normalizer):
    result = normalizer.normalize("binance", "BTC-USDT")
    assert result.original_symbol == "BTC-USDT"
    assert result.exchange == "binance"


def test_does_not_conflate_different_assets(normalizer):
    btc = normalizer.normalize("binance", "BTCUSDT")
    eth = normalizer.normalize("binance", "ETHUSDT")
    assert btc.base_asset != eth.base_asset
    assert btc.pair != eth.pair


def test_unrecognized_symbol_raises(normalizer):
    with pytest.raises(SymbolNormalizationError):
        normalizer.normalize("binance", "???")
