from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dev.db"
    redis_url: str = "redis://localhost:6379/0"
    telegram_bot_token: str = ""
    telegram_webapp_url: str = ""

    # Arbitrage defaults - can be overridden per-request via API filters.
    default_max_data_age_seconds: float = 3.0
    default_min_available_notional: float = 10.0
    default_trade_size_quote: float = 1000.0

    # None means: do not silently drop markets from arbitrage discovery.
    # Capacity/rate limiting belongs to each exchange market-data adapter,
    # not to the global market universe.
    max_tracked_pairs: int | None = None
    # Empty means: track every quote asset. Set a comma-separated list only
    # when an operator intentionally wants to restrict the universe.
    tracked_quote_assets: str = ""

    # Market universe is never capped. This only limits expensive live
    # order-book subscriptions per exchange; deferred markets remain known and
    # can be rotated in by the scheduler later.
    max_live_pairs_per_exchange: int | None = 1000
    live_scheduler_min_exchanges: int = 2
    live_scheduler_rotation_seconds: float = 60.0


settings = Settings()
