from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.exchanges.registry import registry
from app.market_data.manager import MarketDataManager
from app.schemas.api import (
    ExchangeHealthSchema,
    MarketSchema,
    OpportunitySchema,
    OrderBookLevelSchema,
    OrderBookSchema,
)

router = APIRouter(prefix="/api")

# Populated by app.main at startup; kept module-level so route handlers can
# reach the single shared manager instance without FastAPI dependency
# injection boilerplate.
manager: MarketDataManager | None = None


def get_manager() -> MarketDataManager:
    if manager is None:
        raise HTTPException(status_code=503, detail="Market data manager not initialized")
    return manager


@router.get("/exchanges", response_model=list[ExchangeHealthSchema])
async def list_exchanges():
    health = await registry.health_snapshot()
    return [
        ExchangeHealthSchema(
            exchange=h.exchange,
            status=h.status.value,
            markets_count=h.markets_count,
            last_update=h.last_update,
            latency_ms=h.latency_ms,
            reason=h.reason,
        )
        for h in health
    ]


@router.get("/markets", response_model=list[MarketSchema])
async def list_markets(exchange: str | None = None):
    mgr = get_manager()
    results: list[MarketSchema] = []
    for pair, by_exchange in mgr._markets_by_pair.items():  # noqa: SLF001 - internal read-only access
        for ex_name, market in by_exchange.items():
            if exchange and ex_name != exchange:
                continue
            results.append(
                MarketSchema(
                    exchange=market.exchange,
                    symbol=market.symbol,
                    base_asset=market.base_asset,
                    quote_asset=market.quote_asset,
                    status=market.status.value,
                )
            )
    return results


@router.get("/opportunities", response_model=list[OpportunitySchema])
async def list_opportunities(
    min_net_spread: float = Query(0.0),
    min_profit: float = Query(0.0),
    min_volume: float = Query(0.0),
):
    mgr = get_manager()
    opportunities = mgr.latest_opportunities(min_net_spread=min_net_spread)
    opportunities = [
        o for o in opportunities if o.estimated_profit >= min_profit and o.available_volume >= min_volume
    ]
    return [_to_schema(o) for o in opportunities]


@router.get("/opportunities/{opportunity_id}", response_model=OpportunitySchema)
async def get_opportunity(opportunity_id: str):
    mgr = get_manager()
    for opp in mgr.latest_opportunities():
        if opp.id == opportunity_id:
            return _to_schema(opp)
    raise HTTPException(status_code=404, detail="Opportunity not found or no longer active")


@router.get("/orderbook/{exchange}/{symbol}", response_model=OrderBookSchema)
async def get_orderbook(exchange: str, symbol: str):
    try:
        adapter = registry.get(exchange)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown exchange '{exchange}'")
    try:
        book = await adapter.get_orderbook(symbol)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No order book for {symbol} on {exchange}")
    return OrderBookSchema(
        exchange=book.exchange,
        symbol=book.symbol,
        timestamp=book.timestamp,
        bids=[OrderBookLevelSchema(price=l.price, quantity=l.quantity) for l in book.bids],
        asks=[OrderBookLevelSchema(price=l.price, quantity=l.quantity) for l in book.asks],
    )


def _to_schema(o) -> OpportunitySchema:
    return OpportunitySchema(
        id=o.id,
        symbol=o.symbol,
        buy_exchange=o.buy_exchange,
        sell_exchange=o.sell_exchange,
        buy_price=o.buy_price,
        sell_price=o.sell_price,
        gross_spread=o.gross_spread,
        buy_fee=o.buy_fee,
        sell_fee=o.sell_fee,
        estimated_slippage=o.estimated_slippage,
        net_spread=o.net_spread,
        available_volume=o.available_volume,
        recommended_trade_size=o.recommended_trade_size,
        estimated_profit=o.estimated_profit,
        timestamp=o.timestamp,
        data_age=o.data_age,
        status=o.status.value,
    )
