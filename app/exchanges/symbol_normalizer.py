"""
SymbolNormalizer: maps exchange-native symbol spellings to a canonical
(base_asset, quote_asset) pair, without conflating different assets.

Handles the common spellings seen across CEXes:
    BTCUSDT   (no separator)
    BTC/USDT  (slash)
    BTC-USDT  (dash)
    BTC_USDT  (underscore)

Strategy:
  1. If a separator is present (/, -, _), split on it directly.
  2. If no separator, try suffix-matching against a known quote-asset list,
     longest quote first (so "USDT" wins over "T", and "BTCUSDT" doesn't
     get misread as base="BTCUS", quote="DT").

The known quote list is intentionally explicit rather than guessed, since
silently guessing wrong here would corrupt arbitrage matching (spec section 7:
"нельзя ошибочно объединять разные активы").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Ordered longest-first so suffix matching is unambiguous.
KNOWN_QUOTE_ASSETS: list[str] = sorted(
    [
        "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI",
        "USD", "EUR", "GBP", "TRY", "BRL", "RUB",
        "BTC", "ETH", "BNB",
    ],
    key=len,
    reverse=True,
)

SEPARATORS = ("/", "-", "_")


@dataclass(frozen=True)
class NormalizedSymbol:
    base_asset: str
    quote_asset: str
    original_symbol: str
    exchange: str

    @property
    def pair(self) -> str:
        return f"{self.base_asset}/{self.quote_asset}"


class SymbolNormalizationError(Exception):
    pass


class SymbolNormalizer:
    """Stateless normalizer; safe to share across adapters."""

    def __init__(self, extra_quote_assets: Optional[list[str]] = None) -> None:
        quotes = list(KNOWN_QUOTE_ASSETS)
        if extra_quote_assets:
            quotes = sorted(set(quotes) | set(extra_quote_assets), key=len, reverse=True)
        self._quote_assets = quotes

    def normalize(self, exchange: str, raw_symbol: str) -> NormalizedSymbol:
        symbol = raw_symbol.strip().upper()

        for sep in SEPARATORS:
            if sep in symbol:
                parts = symbol.split(sep)
                if len(parts) != 2 or not all(parts):
                    raise SymbolNormalizationError(
                        f"[{exchange}] cannot normalize '{raw_symbol}': "
                        f"unexpected separator layout"
                    )
                base, quote = parts
                return NormalizedSymbol(base, quote, raw_symbol, exchange)

        # No separator: suffix-match against known quote assets.
        for quote in self._quote_assets:
            if symbol.endswith(quote) and len(symbol) > len(quote):
                base = symbol[: -len(quote)]
                return NormalizedSymbol(base, quote, raw_symbol, exchange)

        raise SymbolNormalizationError(
            f"[{exchange}] cannot normalize '{raw_symbol}': no known quote "
            f"asset suffix and no separator found"
        )
