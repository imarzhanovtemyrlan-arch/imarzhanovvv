from __future__ import annotations

from fastapi import APIRouter

from app.exchanges.registry import registry
from app.schemas.api import ExchangeHealthSchema, HealthSchema

router = APIRouter()


@router.get("/health", response_model=HealthSchema)
async def health():
    # Database/Redis checks are best-effort: this endpoint must never crash
    # the whole health check just because one dependency is down (spec 24).
    db_status = "unknown"
    redis_status = "unknown"
    try:
        from app.database.session import check_database

        db_status = "ok" if await check_database() else "error"
    except Exception:  # noqa: BLE001
        db_status = "error"

    try:
        from app.database.redis_client import check_redis

        redis_status = "ok" if await check_redis() else "error"
    except Exception:  # noqa: BLE001
        redis_status = "unavailable"

    return HealthSchema(status="ok", database=db_status, redis=redis_status)


@router.get("/health/exchanges", response_model=list[ExchangeHealthSchema])
async def health_exchanges():
    snapshot = await registry.health_snapshot()
    return [
        ExchangeHealthSchema(
            exchange=h.exchange,
            status=h.status.value,
            markets_count=h.markets_count,
            last_update=h.last_update,
            latency_ms=h.latency_ms,
            reason=h.reason,
        )
        for h in snapshot
    ]
