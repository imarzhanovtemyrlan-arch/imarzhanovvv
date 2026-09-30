"""
MarketDataManager: sits between ExchangeRegistry and ArbitrageEngine.

Responsibilities:
  - fetch markets from every registered (supported) exchange
  - normalize symbols and group markets by normalized pair (BASE/QUOTE)
  - subscribe to order books for symbols traded on 2+ exchanges (arbitrage
    is only possible when at least two exchanges list the same pair)
  - keep the latest OrderBook per (exchange, symbol) in memory
  - periodically (or on every update) run the ArbitrageEngine and publish
    results via a pluggable `on_opportunities` callback (used by the
    WebSocket layer to push to connected clients)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Callable, Optional

from app.arbitrage.engine import ArbitrageEngine, ExchangeSnapshot
from app.arbitrage.filters import FilterConfig
from app.exchanges.base import ExchangeAdapter
from app.exchanges.registry import ExchangeRegistry
from app.exchanges.symbol_normalizer import SymbolNormalizer
from app.models.market import ArbitrageOpportunity, ExchangeFees, ExchangeMarket, OrderBook
from app.market_data.scheduler import MarketScheduler, SchedulerStats

logger = logging.getLogger(__name__)

OpportunitiesCallback = Callable[[str, list[ArbitrageOpportunity]], None]


class MarketDataManager:
    def __init__(
        self,
        registry: ExchangeRegistry,
        engine: Optional[ArbitrageEngine] = None,
        normalizer: Optional[SymbolNormalizer] = None,
        default_trade_size: float = 1000.0,
        max_tracked_pairs: Optional[int] = None,
        tracked_quote_assets: Optional[set[str]] = None,
        max_live_pairs_per_exchange: Optional[int] = 1000,
        scheduler_min_exchanges: int = 2,
        scheduler_rotation_seconds: float = 60.0,
    ) -> None:
        self.registry = registry
        self.engine = engine or ArbitrageEngine()
        self.normalizer = normalizer or SymbolNormalizer()
        self.default_trade_size = default_trade_size
        self.max_tracked_pairs = max_tracked_pairs
        self.tracked_quote_assets = tracked_quote_assets
        self.scheduler_rotation_seconds = scheduler_rotation_seconds
        self.scheduler = MarketScheduler(
            max_live_pairs_per_exchange=max_live_pairs_per_exchange,
            min_exchanges=scheduler_min_exchanges,
        )
        self._scheduler_stats = SchedulerStats(0, 0, 0, {}, 0)
        self._scheduler_rotation = 0
        self._scheduler_task: asyncio.Task | None = None
        self._live_subscriptions: set[tuple[str, str]] = set()

        # normalized_pair -> exchange -> ExchangeMarket
        self._markets_by_pair: dict[str, dict[str, ExchangeMarket]] = defaultdict(dict)
        # (exchange, original_symbol) -> OrderBook
        self._latest_books: dict[tuple[str, str], OrderBook] = {}
        # exchange -> fees cache. Adapters currently expose account-independent
        # default spot fees, so fetch once per exchange instead of once per symbol.
        self._fees_cache: dict[tuple[str, str], ExchangeFees] = {}
        self._latest_opportunities_by_pair: dict[str, list[ArbitrageOpportunity]] = {}

        self._on_opportunities: list[OpportunitiesCallback] = []

    def on_opportunities(self, callback: OpportunitiesCallback) -> None:
        self._on_opportunities.append(callback)

    async def discover_markets(self) -> None:
        """Fetch and normalize markets from every connected exchange."""
        for name, adapter in self.registry.all_instances().items():
            try:
                markets = await adapter.get_markets()
            except Exception:  # noqa: BLE001 - one bad exchange must not stop discovery
                logger.exception("Failed to fetch markets from %s", name)
                continue
            for market in markets:
                try:
                    normalized = self.normalizer.normalize(name, market.symbol)
                except Exception:  # noqa: BLE001
                    logger.warning("Skipping unnormalizable symbol %s on %s", market.symbol, name)
                    continue
                self._markets_by_pair[normalized.pair][name] = market

    def tradable_pairs(self, min_exchanges: int = 2) -> list[str]:
        """Return the complete arbitrage market universe.

        There is deliberately no implicit global cap: dropping markets here
        means an arbitrage opportunity can never be discovered. Any API/WS
        capacity limit must be enforced by the exchange adapter while the
        normalized market universe remains complete.
        """
        candidates = [
            pair
            for pair, by_exchange in self._markets_by_pair.items()
            if len(by_exchange) >= min_exchanges
            and (
                self.tracked_quote_assets is None
                or pair.split("/")[-1] in self.tracked_quote_assets
            )
        ]
        candidates.sort()
        if self.max_tracked_pairs is not None:
            # Kept only as an explicit operator override/backward-compatible
            # safety valve; production defaults to None.
            candidates = candidates[: self.max_tracked_pairs]
        return candidates

    async def subscribe_all(self) -> None:
        """Start the first live-data window and its rotation loop."""
        await self._apply_subscription_window()
        if self.scheduler.max_live_pairs_per_exchange is not None:
            if self._scheduler_task is None:
                self._scheduler_task = asyncio.create_task(self._rotation_loop())

    async def _apply_subscription_window(self) -> None:
        plan, stats = self.scheduler.build_plan(
            self._markets_by_pair, rotation=self._scheduler_rotation
        )
        self._scheduler_stats = stats
        desired = {(item.exchange, item.pair) for item in plan}

        # Unsubscribe only markets leaving the live window.
        left_pairs: set[str] = set()
        for exchange, pair in self._live_subscriptions - desired:
            market = self._markets_by_pair.get(pair, {}).get(exchange)
            if market is not None:
                try:
                    await self.registry.get(exchange).unsubscribe_orderbook(market.symbol)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to unsubscribe %s on %s", pair, exchange)
                # A book that no longer receives updates must not keep feeding
                # opportunities: drop it and recompute/publish the pair now.
                self._latest_books.pop((exchange, market.symbol), None)
                left_pairs.add(pair)
        for pair in left_pairs:
            opportunities = self._compute_opportunities_for_pair(pair)
            if opportunities:
                self._latest_opportunities_by_pair[pair] = opportunities
            else:
                self._latest_opportunities_by_pair.pop(pair, None)
            for cb in self._on_opportunities:
                cb(pair, opportunities)

        async def subscribe(item) -> None:
            adapter = self.registry.get(item.exchange)
            await adapter.subscribe_orderbook(
                item.market.symbol,
                lambda book, ex=item.exchange, p=item.pair: self._handle_book_update(p, ex, book),
            )

        semaphore = asyncio.Semaphore(20)

        async def guarded(item) -> None:
            async with semaphore:
                if (item.exchange, item.pair) not in self._live_subscriptions:
                    try:
                        await subscribe(item)
                    except Exception:  # noqa: BLE001
                        logger.exception("Failed to subscribe %s on %s", item.pair, item.exchange)

        await asyncio.gather(*(guarded(item) for item in plan))
        self._live_subscriptions = desired
        logger.info(
            "Live scheduler: rotation=%d universe=%d candidates=%d live_subscriptions=%d deferred=%d per_exchange=%s",
            self._scheduler_rotation, stats.universe_pairs, stats.candidate_pairs,
            stats.live_subscriptions, stats.deferred_pairs, stats.live_pairs_by_exchange,
        )

    async def _rotation_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(max(5.0, self.scheduler_rotation_seconds))
                self._scheduler_rotation += 1
                await self._apply_subscription_window()
        except asyncio.CancelledError:
            return

    @property
    def scheduler_stats(self) -> SchedulerStats:
        return self._scheduler_stats

    def _handle_book_update(self, pair: str, exchange: str, book: OrderBook) -> None:
        self._latest_books[(exchange, book.symbol)] = book
        # Recompute opportunities for this pair synchronously; callers running
        # under asyncio should keep this handler cheap (no network calls here).
        opportunities = self._compute_opportunities_for_pair(pair)
        self._latest_opportunities_by_pair[pair] = opportunities
        for cb in self._on_opportunities:
            # Publish an empty update too: the frontend must be able to remove
            # an opportunity as soon as its spread disappears.
            cb(pair, opportunities)

    def _compute_opportunities_for_pair(self, pair: str) -> list[ArbitrageOpportunity]:
        by_exchange = self._markets_by_pair.get(pair, {})
        snapshots: list[ExchangeSnapshot] = []
        for exchange_name, market in by_exchange.items():
            book = self._latest_books.get((exchange_name, market.symbol))
            if book is None:
                continue
            fees = self._fees_cache.get((exchange_name, market.symbol)) or self._fees_cache.get((exchange_name, ""))
            if fees is None:
                # Fees should be pre-fetched via refresh_fees(); fall back to
                # zero-fee only as a last resort so a missing cache entry
                # never silently overstates profit.
                fees = ExchangeFees(exchange=exchange_name, symbol=market.symbol, maker_fee=0.0, taker_fee=0.0)
            snapshots.append(ExchangeSnapshot(exchange=exchange_name, market=market, order_book=book, fees=fees))

        if len(snapshots) < 2:
            return []
        return self.engine.find_opportunities(pair, snapshots, trade_size_quote=self.default_trade_size)

    async def refresh_fees(self) -> None:
        """Load account-independent default fees once per exchange.

        Calling get_fees() for every symbol turns startup into O(exchanges *
        markets) API calls even though the current adapters return the same
        default rate for every spot symbol. The per-symbol cache is populated
        from that single exchange-level result.
        """
        for exchange_name in self.registry.all_instances():
            adapter = self.registry.get(exchange_name)
            try:
                fees = await adapter.get_fees(None)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to fetch default fees on %s", exchange_name)
                continue
            self._fees_cache[(exchange_name, "")] = fees
            for market in self._markets_by_pair_by_exchange(exchange_name):
                self._fees_cache[(exchange_name, market.symbol)] = ExchangeFees(
                    exchange=exchange_name,
                    symbol=market.symbol,
                    maker_fee=fees.maker_fee,
                    taker_fee=fees.taker_fee,
                )

    def _markets_by_pair_by_exchange(self, exchange_name: str) -> list[ExchangeMarket]:
        return [
            market
            for by_exchange in self._markets_by_pair.values()
            for name, market in by_exchange.items()
            if name == exchange_name
        ]

    def latest_opportunities(self, min_net_spread: float = 0.0) -> list[ArbitrageOpportunity]:
        """Return the cached live results; never recompute the whole market on HTTP requests."""
        all_opps = [
            opportunity
            for opportunities in self._latest_opportunities_by_pair.values()
            for opportunity in opportunities
        ]
        return [o for o in all_opps if o.net_spread >= min_net_spread]