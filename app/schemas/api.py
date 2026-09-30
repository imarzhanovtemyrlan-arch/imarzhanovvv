from __future__ import annotations

from pydantic import BaseModel


class OpportunitySchema(BaseModel):
    id: str
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    gross_spread: float
    buy_fee: float
    sell_fee: float
    estimated_slippage: float
    net_spread: float
    available_volume: float
    recommended_trade_size: float
    estimated_profit: float
    timestamp: float
    data_age: float
    status: str


class ExchangeHealthSchema(BaseModel):
    exchange: str
    status: str
    markets_count: int
    last_update: float | None
    latency_ms: float | None
    reason: str | None = None


class MarketSchema(BaseModel):
    exchange: str
    symbol: str
    base_asset: str
    quote_asset: str
    status: str


class OrderBookLevelSchema(BaseModel):
    price: float
    quantity: float


class OrderBookSchema(BaseModel):
    exchange: str
    symbol: str
    timestamp: float
    bids: list[OrderBookLevelSchema]
    asks: list[OrderBookLevelSchema]


class HealthSchema(BaseModel):
    status: str
    database: str
    redis: str


class TelegramAuthRequest(BaseModel):
    init_data: str


class TelegramUserSchema(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool = False


class TelegramAuthResponse(BaseModel):
    user: TelegramUserSchema | None
    auth_date: int


class OpportunityFilters(BaseModel):
    min_net_spread: float = 0.0
    min_profit: float = 0.0
    min_volume: float = 0.0
    max_data_age: float = 3.0
    exchanges: list[str] | None = None
    quote_currencies: list[str] | None = None
    assets: list[str] | None = None
