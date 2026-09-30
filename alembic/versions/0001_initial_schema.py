"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-27

Hand-written to mirror app/database/models.py exactly (spec section 17:
exchanges, markets, fees, opportunities, system_events, user_settings).
Written by hand rather than via `alembic revision --autogenerate` since
that requires a live database connection this environment doesn't have;
double-check with `alembic check` against a real Postgres/SQLite instance
before relying on this in production.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exchanges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("is_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_exchanges_name", "exchanges", ["name"], unique=True)

    op.create_table(
        "markets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("base_asset", sa.String(length=32), nullable=False),
        sa.Column("quote_asset", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("tick_size", sa.Float(), nullable=False),
        sa.Column("step_size", sa.Float(), nullable=False),
        sa.Column("min_quantity", sa.Float(), nullable=False),
        sa.Column("min_notional", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_markets_exchange", "markets", ["exchange"])
    op.create_index("ix_markets_symbol", "markets", ["symbol"])

    op.create_table(
        "fees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=True),
        sa.Column("maker_fee", sa.Float(), nullable=False),
        sa.Column("taker_fee", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_fees_exchange", "fees", ["exchange"])

    op.create_table(
        "opportunities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("buy_exchange", sa.String(length=64), nullable=False),
        sa.Column("sell_exchange", sa.String(length=64), nullable=False),
        sa.Column("net_spread", sa.Float(), nullable=False),
        sa.Column("estimated_profit", sa.Float(), nullable=False),
        sa.Column("available_volume", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_opportunities_symbol", "opportunities", ["symbol"])

    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.String(length=64), nullable=False),
        sa.Column("min_net_spread", sa.Float(), nullable=False, server_default="0.003"),
        sa.Column("min_profit", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("min_volume", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("max_data_age", sa.Float(), nullable=False, server_default="3.0"),
    )
    op.create_index("ix_user_settings_telegram_user_id", "user_settings", ["telegram_user_id"], unique=True)


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_table("system_events")
    op.drop_index("ix_opportunities_symbol", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_index("ix_fees_exchange", table_name="fees")
    op.drop_table("fees")
    op.drop_index("ix_markets_symbol", table_name="markets")
    op.drop_index("ix_markets_exchange", table_name="markets")
    op.drop_table("markets")
    op.drop_index("ix_exchanges_name", table_name="exchanges")
    op.drop_table("exchanges")
