"""
Redis is used for hot-path data: latest order books, ticker cache, exchange
status, opportunity cache, pub/sub (spec section 18).

If Redis is unavailable (e.g. local dev without docker), the app must still
run - callers should treat `get_redis()` returning None as "use in-memory
fallback only" rather than crash.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None
_attempted = False


def get_redis():
    global _client, _attempted
    if _attempted:
        return _client
    _attempted = True
    try:
        import redis.asyncio as redis

        _client = redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:  # noqa: BLE001
        logger.warning("Redis unavailable, falling back to in-memory-only mode")
        _client = None
    return _client


async def check_redis() -> bool:
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001
        return False
