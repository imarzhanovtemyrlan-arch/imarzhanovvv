# Architecture

## Overview

```
Telegram Mini App (React + TS, mobile-first)
        |
        | REST (/api/*) + WebSocket (/ws/opportunities)
        v
FastAPI Backend (app/main.py)
        |
        v
MarketDataManager (app/market_data/manager.py)
        |         \
        |          \--> SymbolNormalizer (app/exchanges/symbol_normalizer.py)
        v
ExchangeRegistry (app/exchanges/registry.py)
        |
        v
ExchangeAdapter implementations (app/exchanges/adapters/*)
  - MockExchangeAdapter  (tests / local dev, no network)
  - BinanceAdapter       (real, public REST + WS, no API key)
  - ... add more by subclassing ExchangeAdapter
        |
        v
ArbitrageEngine (app/arbitrage/engine.py)
  - filters.py    -> reject stale/halted/mismatched/implausible signals
  - vwap.py        -> walk real order book depth
  - calculator.py  -> fees + slippage + net spread + profit
        |
        v
ArbitrageOpportunity objects
        |
        +--> Postgres/SQLite (periodic snapshots, app/database/models.py)
        +--> Redis (hot cache, app/database/redis_client.py, optional)
        +--> WebSocket broadcast to connected Mini App clients
```

## Design principles

1. **Core never depends on exchange-specific formats.** Every adapter
   normalizes into `app.models.market` dataclasses before anything else in
   the system sees the data.
2. **No hardcoded exchange list.** `ExchangeRegistry` is populated by
   `app/exchanges/adapters/__init__.py` calling `registry.register(...)`.
   Adding an exchange never requires touching the arbitrage engine, the API,
   or the registry implementation itself.
3. **Correctness over coverage.** An exchange whose API can't be reliably
   integrated is marked `UNSUPPORTED` with a reason, rather than faking data.
   A "connected" exchange status is a promise the rest of the system relies on.
4. **WebSocket first, REST as fallback/recovery.** Per adapter, live order
   books come from WebSocket; REST is used for exchangeInfo/metadata, initial
   snapshots, and recovery after a disconnect.
5. **Never trust best bid/ask alone.** All profit figures come from VWAP
   across real order book depth for the requested trade size.
6. **False signals are filtered before profit is even computed** - see
   `docs/arbitrage-engine.md`.

## Data flow for one opportunity

1. `MarketDataManager.discover_markets()` calls `get_markets()` on every
   registered exchange, normalizes each symbol, and groups markets by
   normalized pair (`BTC/USDT`, etc).
2. `MarketDataManager.subscribe_all()` subscribes to live order books only
   for pairs listed on 2+ exchanges (arbitrage is impossible otherwise).
3. Each order book update triggers `_handle_book_update`, which stores the
   latest book and re-runs `ArbitrageEngine.find_opportunities` for that pair.
4. Opportunities that pass all filters and have `net_spread > 0` are pushed
   to `/ws/opportunities` and available via `GET /api/opportunities`.

## Persistence split (spec section 17-18)

- **Postgres/SQLite**: exchanges, markets, symbols, fees, periodic
  opportunity snapshots, system events, user settings. NOT per-tick data.
- **Redis** (optional - app runs without it in local dev): latest order
  books, ticker cache, exchange status, opportunity cache, pub/sub. If Redis
  is unreachable, `app.database.redis_client.get_redis()` returns `None` and
  callers fall back to in-memory-only state.

## Execution engine (future, currently disabled)

Spec section 30 calls for the architecture to support a future
`ExecutionEngine` without building it now. The extension point is
`ExchangeAdapter` itself - once ready, a new module would call the same
adapters' (currently unimplemented) order-placement methods, gated behind an
explicit feature flag. No such code exists yet; this project is monitoring-only.

## Connection management

Adapters never open a WebSocket per symbol. `MultiplexedOrderbookStream`
(`app/exchanges/adapters/_ws_common.py`) owns ONE connection per exchange,
adds symbols with incremental (chunked) subscribe frames, resubscribes
everything after a reconnect (exponential backoff), and runs the
exchange-specific heartbeat. `MarketDataManager.tradable_pairs()` bounds how
many symbols are subscribed (`MAX_TRACKED_PAIRS`, `TRACKED_QUOTE_ASSETS`).
