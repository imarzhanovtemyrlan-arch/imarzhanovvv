# Universal CEX Spot Arbitrage Monitor

A Telegram Mini App that monitors spot order books across multiple
centralized crypto exchanges in real time, and surfaces real (fee- and
slippage-adjusted) arbitrage opportunities.

**This is monitoring only.** No trading, no order placement, no exchange API
keys required. See [Limitations](#limitations).

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full diagram and
design principles. In short:

```
Telegram Mini App (React+TS) -> FastAPI Backend -> Market Data Manager
                                                  -> Exchange Registry (pluggable adapters)
                                                  -> Arbitrage Engine (VWAP + fees + slippage)
                                                  -> Postgres (persistent) + Redis (hot cache)
                                                  -> WebSocket (/ws/opportunities)
```

## What's implemented

- Universal `ExchangeAdapter` interface + dynamic `ExchangeRegistry` -
  adding an exchange never touches the arbitrage engine (see
  [docs/exchange-adapter.md](docs/exchange-adapter.md))
- `SymbolNormalizer` reconciling `BTCUSDT` / `BTC/USDT` / `BTC-USDT` / `BTC_USDT`
- **Real adapters, all public endpoints, no API keys:**
  - **Binance** - `exchangeInfo`/`depth`/`bookTicker` REST, `depth20@100ms` WS
  - **Bybit** (V5 unified) - `instruments-info`/`orderbook`/`tickers` REST,
    `orderbook.50.<symbol>` WS with correct snapshot+delta merging
  - **OKX** (V5) - `instruments`/`books`/`ticker` REST, `books5` WS
    (snapshot-per-update, top 5 levels)
  - **Gate.io** (V4) - `currency_pairs`/`order_book`/`tickers` REST,
    `spot.order_book` WS (snapshot-per-update)
  - **MEXC** (spot v3) - REST **polling only**, deliberately - MEXC's public
    order-book WebSocket is Protobuf-encoded and decoding it correctly
    requires MEXC's `.proto` schemas, which aren't available here; see the
    docstring in `app/exchanges/adapters/mexc.py` for the reasoning and the
    upgrade path
  - The four WebSocket-based adapters: reconnect with exponential backoff +
    heartbeat, REST fallback for initial snapshot/recovery
- `MockExchangeAdapter` for tests and offline development
- **Telegram `initData` server-side verification** (`app/telegram/verify.py`,
  `POST /api/auth/telegram`) - implements Telegram's documented HMAC-SHA256
  algorithm exactly; the frontend never trusts `initData` itself, only sends
  it here for verification (spec section 12)
- Arbitrage Engine: VWAP-based profit across real order book depth, fee and
  slippage accounting, false-signal filtering (stale data, halted markets,
  mismatched quote currencies, implausible spreads, thin volume) - see
  [docs/arbitrage-engine.md](docs/arbitrage-engine.md)
- FastAPI backend: REST endpoints, `/ws/opportunities` WebSocket,
  `/health` and `/health/exchanges`
- Telegram bot (`/start` -> opens the Mini App)
- React + TypeScript + Vite Mini App: Arbitrage list with filters, Exchanges
  status screen, Pair Details with VWAP sizing table ($100-$10,000)
- Postgres/SQLite models for durable data; Redis for hot-path caching with
  automatic in-memory fallback if Redis is unavailable
- Alembic migrations (`backend/alembic/`) for the schema in
  `app/database/models.py`, wired to the app's own `DATABASE_URL` and async
  engine
- Test suite covering symbol normalization, VWAP, fee/slippage/profit
  calculation, false-signal filters, the engine end-to-end, the mock
  adapter, and the registry

## Connection & rate-limit design (important)

- **One WebSocket connection per exchange**, never per symbol
  (`backend/app/exchanges/adapters/_ws_common.py`). Symbols are added to the
  live connection via incremental subscribe frames, chunked to respect
  per-request topic limits, with a short pause between frames. On reconnect
  every known symbol is resubscribed. Regression tests:
  `backend/tests/test_ws_multiplexing.py`, `test_subscription_limits.py`.
- **MEXC** (REST polling) uses one shared loop with a concurrency cap, not a
  task per symbol.
- **Tracked-pair budget**: only pairs listed on 2+ exchanges, restricted to
  `TRACKED_QUOTE_ASSETS` (default `USDT,USDC`), capped at `MAX_TRACKED_PAIRS`
  (default 200), prioritizing pairs listed on the most exchanges. Raise the
  cap gradually and watch the logs - long-tail tokens that merely share a
  ticker across exchanges are rarely real arbitrage (and can be *different
  assets* with the same ticker), so a smaller list is also a correctness win.

## What's NOT implemented yet

- Additional real exchange adapters beyond Binance/Bybit/OKX/Gate.io/MEXC
  (Bitget, KuCoin, Kraken, Coinbase, HTX, Bitfinex, etc.) - the architecture
  supports adding them without any core changes; see
  [docs/exchange-adapter.md](docs/exchange-adapter.md) for the process
- MEXC's real-time WebSocket (Protobuf-encoded; needs MEXC's `.proto`
  schemas vendored in - currently REST polling only, see
  `app/exchanges/adapters/mexc.py`)
- OKX's deeper `books` channel (delta-based, like Bybit's) - currently uses
  `books5` (snapshot-per-update, 5 levels) for simplicity/correctness; see
  the docstring in `app/exchanges/adapters/okx.py`
- `ExecutionEngine` - intentionally not built; see spec section 30 and
  [docs/architecture.md](docs/architecture.md#execution-engine-future-currently-disabled)

## Running with Docker (recommended)

```bash
cp .env.example .env
docker compose up
```

This starts Postgres, Redis, the FastAPI backend (port 8000), and the Vite
dev server (port 5173). Open the frontend URL in a browser for local
testing; to test inside Telegram, set `TELEGRAM_WEBAPP_URL` in `.env` to a
publicly reachable HTTPS URL for the frontend (e.g. via ngrok during
development) and run the bot (see below).

## Running locally without Docker

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # edit as needed; sqlite works with no changes
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Database migrations

```bash
cd backend
alembic upgrade head
```

Reads `DATABASE_URL` from the same `.env`/`app.core.config.settings` the app
uses - no separate connection string to maintain. To generate a new
migration after changing `app/database/models.py`:

```bash
alembic revision --autogenerate -m "describe the change"
```

### Telegram bot

```bash
cd backend
python -m app.telegram.bot
```

Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBAPP_URL` set in `.env`.
Get a bot token from [@BotFather](https://t.me/BotFather); `/newapp` sets up
the Mini App URL.

## Running tests

```bash
cd backend
pip install -r requirements.txt
pytest
```

Tests use `MockExchangeAdapter` exclusively - no real exchange API or
network access required.

## Adding a new exchange

See [docs/exchange-adapter.md](docs/exchange-adapter.md). Summary: create
`backend/app/exchanges/adapters/<name>.py` implementing `ExchangeAdapter`,
register it in `backend/app/exchanges/adapters/__init__.py`. No other file
needs to change.

## Environment variables

See [.env.example](.env.example) for the full list. Highlights:

| Variable | Purpose | Required? |
|---|---|---|
| `DATABASE_URL` | Postgres (prod) or SQLite (dev) connection string | No - defaults to local SQLite |
| `REDIS_URL` | Redis connection string | No - app runs without Redis (in-memory fallback) |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | Only to run the Telegram bot |
| `TELEGRAM_WEBAPP_URL` | Public HTTPS URL of the deployed frontend | Only to run the Telegram bot |

**No exchange API keys are required or used** - this is monitoring-only mode
(spec section 21).

## Limitations

- Monitoring only. No order placement, no execution, no exchange API keys
  used for trading. An `ExecutionEngine` extension point is documented but
  intentionally not implemented.
- Fees for all wired-up exchanges use each exchange's documented default
  public rate as an approximation, not an account-specific fetch - see each
  adapter's docstring for specifics.
- Five real exchanges (Binance, Bybit, OKX, Gate.io, MEXC) are wired up in
  this iteration; the registry/adapter architecture is designed for many
  more, added incrementally following `docs/exchange-adapter.md`. MEXC is
  REST-polling only (see above), so its data is lower-frequency than the
  other four.
- `initData` verification exists (`POST /api/auth/telegram`) but nothing in
  this build yet *uses* the verified identity for personalization (e.g.
  per-user saved filters) - it's called on app load and the result is
  currently just discarded by the frontend.

## Market universe and live scheduling

The system keeps the full normalized market universe by default; it does not
silently restrict discovery to USDT/USDC or to a fixed number of pairs.
Expensive live order-book subscriptions are controlled separately per exchange
with `MAX_LIVE_PAIRS_PER_EXCHANGE` (default `1000`). Markets above that live
capacity remain in the universe and are deferred by the priority scheduler.
Set `MAX_LIVE_PAIRS_PER_EXCHANGE=` to an empty value only when the deployment
can safely monitor every candidate market live.
