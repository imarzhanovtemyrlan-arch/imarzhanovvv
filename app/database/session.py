"""
Async SQLAlchemy engine/session setup.

Persisted here per spec section 17: exchanges, markets, symbols, fees,
opportunities (periodic snapshots, not every tick), system_events,
user_settings. High-frequency tick data stays in memory/Redis - see
app.database.redis_client and app.market_data.manager.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


async def check_database() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False
