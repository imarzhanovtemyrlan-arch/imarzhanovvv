from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, routes
from app.core.config import settings
from app.exchanges import adapters  # noqa: F401 - side-effect: registers all adapters
from app.exchanges.registry import registry
from app.market_data.manager import MarketDataManager
from app.websocket.opportunities_ws import connection_manager, opportunities_to_payload
from app.websocket.opportunities_ws import router as ws_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = MarketDataManager(
        registry,
        default_trade_size=settings.default_trade_size_quote,
        max_tracked_pairs=settings.max_tracked_pairs,
        tracked_quote_assets={q.strip().upper() for q in settings.tracked_quote_assets.split(",") if q.strip()} or None,
        max_live_pairs_per_exchange=settings.max_live_pairs_per_exchange,
        scheduler_min_exchanges=settings.live_scheduler_min_exchanges,
        scheduler_rotation_seconds=settings.live_scheduler_rotation_seconds,
    )

    def push_to_websocket(pair: str, opportunities) -> None:
        # Fire-and-forget: broadcast is async but this callback is sync
        # (called from adapter callbacks). Schedule it on the loop instead
        # of blocking the market data path.
        import asyncio

        payload = opportunities_to_payload(pair, opportunities)
        asyncio.create_task(connection_manager.broadcast(payload))

    manager.on_opportunities(push_to_websocket)

    await registry.connect_all()
    await manager.discover_markets()
    await manager.refresh_fees()
    await manager.subscribe_all()

    routes.manager = manager
    app.state.manager = manager

    logger.info(
        "Startup complete. Registered exchanges: %s", registry.registered_names()
    )
    yield

    if getattr(manager, "_scheduler_task", None) is not None:
        manager._scheduler_task.cancel()
    await registry.disconnect_all()


app = FastAPI(title="Universal CEX Spot Arbitrage Monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Telegram Mini App runs in a webview; tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(routes.router)
app.include_router(auth.router)
app.include_router(ws_router)
