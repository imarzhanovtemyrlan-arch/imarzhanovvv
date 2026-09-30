"""
/ws/opportunities: pushes newly computed ArbitrageOpportunity batches to all
connected clients in real time (spec section 19 - "Frontend должен получать
новые возможности в реальном времени").
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.market import ArbitrageOpportunity

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


connection_manager = ConnectionManager()


def opportunities_to_payload(pair: str, opportunities: list[ArbitrageOpportunity]) -> dict:
    return {
        "type": "opportunities_update",
        "pair": pair,
        "opportunities": [
            {
                "id": o.id,
                "symbol": o.symbol,
                "buy_exchange": o.buy_exchange,
                "sell_exchange": o.sell_exchange,
                "buy_price": o.buy_price,
                "sell_price": o.sell_price,
                "gross_spread": o.gross_spread,
                "net_spread": o.net_spread,
                "estimated_profit": o.estimated_profit,
                "available_volume": o.available_volume,
                "data_age": o.data_age,
                "status": o.status.value,
            }
            for o in opportunities
        ],
    }


@router.websocket("/ws/opportunities")
async def opportunities_ws(websocket: WebSocket) -> None:
    await connection_manager.connect(websocket)
    try:
        while True:
            # Client isn't expected to send anything meaningful; we just keep
            # the connection alive and detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket)
