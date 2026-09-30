"""Market-universe to live-orderbook scheduling.

The universe is intentionally unbounded: every pair remains discoverable.
Only the expensive live-orderbook layer is capacity limited, independently per
exchange.  This prevents a large market universe from turning into thousands
of concurrent REST/WS jobs while preserving every market for future rotation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.models.market import ExchangeMarket


@dataclass(frozen=True)
class LiveSubscription:
    pair: str
    exchange: str
    market: ExchangeMarket
    priority: float


@dataclass(frozen=True)
class SchedulerStats:
    universe_pairs: int
    candidate_pairs: int
    live_subscriptions: int
    live_pairs_by_exchange: dict[str, int]
    deferred_pairs: int


class MarketScheduler:
    """Build a bounded live-data plan without truncating the market universe."""

    DEFAULT_PRIORITY_QUOTES = {
        "USDT": 100.0,
        "USDC": 95.0,
        "FDUSD": 90.0,
        "USDE": 88.0,
        "USD": 85.0,
        "EUR": 70.0,
        "BTC": 60.0,
        "ETH": 55.0,
    }

    def __init__(
        self,
        *,
        max_live_pairs_per_exchange: int | None = 1000,
        min_exchanges: int = 2,
        priority_quotes: Mapping[str, float] | None = None,
    ) -> None:
        if max_live_pairs_per_exchange is not None and max_live_pairs_per_exchange < 1:
            raise ValueError("max_live_pairs_per_exchange must be >= 1 or None")
        if min_exchanges < 2:
            raise ValueError("min_exchanges must be >= 2")
        self.max_live_pairs_per_exchange = max_live_pairs_per_exchange
        self.min_exchanges = min_exchanges
        self.priority_quotes = {
            **self.DEFAULT_PRIORITY_QUOTES,
            **{k.upper(): float(v) for k, v in (priority_quotes or {}).items()},
        }

    def candidate_pairs(self, markets_by_pair: Mapping[str, Mapping[str, ExchangeMarket]]) -> list[str]:
        return sorted(
            pair for pair, by_exchange in markets_by_pair.items()
            if len(by_exchange) >= self.min_exchanges
        )

    def build_plan(
        self,
        markets_by_pair: Mapping[str, Mapping[str, ExchangeMarket]],
        *,
        rotation: int = 0,
    ) -> tuple[list[LiveSubscription], SchedulerStats]:
        candidates = self.candidate_pairs(markets_by_pair)
        scored: list[tuple[float, str]] = []
        for pair in candidates:
            quote = pair.rsplit("/", 1)[-1].upper()
            score = self.priority_quotes.get(quote, 10.0)
            score += len(markets_by_pair[pair]) * 20.0
            scored.append((score, pair))
        scored.sort(key=lambda x: (-x[0], x[1]))

        # Rotate the candidate order by whole pairs. This keeps all exchange
        # legs of an arbitrage pair in the same live window.
        ordered_pairs = [pair for _, pair in scored]
        if ordered_pairs and rotation and self.max_live_pairs_per_exchange is not None:
            offset = (rotation * self.max_live_pairs_per_exchange) % len(ordered_pairs)
            ordered_pairs = ordered_pairs[offset:] + ordered_pairs[:offset]

        used: dict[str, int] = {}
        selected: list[LiveSubscription] = []
        selected_pairs: set[str] = set()
        for pair in ordered_pairs:
            venues = markets_by_pair[pair]
            if self.max_live_pairs_per_exchange is not None:
                if any(used.get(exchange, 0) >= self.max_live_pairs_per_exchange for exchange in venues):
                    continue
            score = next(score for score, p in scored if p == pair)
            for exchange, market in venues.items():
                selected.append(LiveSubscription(pair, exchange, market, score))
                used[exchange] = used.get(exchange, 0) + 1
            selected_pairs.add(pair)

        selected.sort(key=lambda x: (-x.priority, x.pair, x.exchange))
        stats = SchedulerStats(
            universe_pairs=len(markets_by_pair),
            candidate_pairs=len(candidates),
            live_subscriptions=len(selected),
            live_pairs_by_exchange={
                exchange: count for exchange, count in sorted(used.items())
            },
            deferred_pairs=sum(1 for pair in candidates if pair not in selected_pairs),
        )
        return selected, stats
