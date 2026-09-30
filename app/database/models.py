"""
SQLAlchemy ORM tables. Only durable/periodic data lives here - never raw
per-tick order book updates (those stay in Redis/memory, spec section 17-18).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ExchangeRecord(Base):
    __tablename__ = "exchanges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class MarketRecord(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    base_asset: Mapped[str] = mapped_column(String(32))
    quote_asset: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    tick_size: Mapped[float] = mapped_column(Float)
    step_size: Mapped[float] = mapped_column(Float)
    min_quantity: Mapped[float] = mapped_column(Float)
    min_notional: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class FeeRecord(Base):
    __tablename__ = "fees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    maker_fee: Mapped[float] = mapped_column(Float)
    taker_fee: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class OpportunityRecord(Base):
    """Periodic snapshot of surfaced opportunities, for history/analytics.
    NOT written on every order book tick - see spec section 17."""

    __tablename__ = "opportunities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    buy_exchange: Mapped[str] = mapped_column(String(64))
    sell_exchange: Mapped[str] = mapped_column(String(64))
    net_spread: Mapped[float] = mapped_column(Float)
    estimated_profit: Mapped[float] = mapped_column(Float)
    available_volume: Mapped[float] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class SystemEventRecord(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class UserSettingsRecord(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    min_net_spread: Mapped[float] = mapped_column(Float, default=0.003)
    min_profit: Mapped[float] = mapped_column(Float, default=0.0)
    min_volume: Mapped[float] = mapped_column(Float, default=0.0)
    max_data_age: Mapped[float] = mapped_column(Float, default=3.0)
