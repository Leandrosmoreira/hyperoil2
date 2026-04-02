"""SQLAlchemy ORM models for persistent storage."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TickRecord(Base):
    __tablename__ = "ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    bid: Mapped[float] = mapped_column(Float, nullable=False)
    ask: Mapped[float] = mapped_column(Float, nullable=False)
    mid: Mapped[float] = mapped_column(Float, nullable=False)
    last: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_ticks_symbol_ts", "symbol", "timestamp_ms"),
    )


class FeatureRecord(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    price_left: Mapped[float] = mapped_column(Float, nullable=False)
    price_right: Mapped[float] = mapped_column(Float, nullable=False)
    beta: Mapped[float] = mapped_column(Float, nullable=False)
    spread: Mapped[float] = mapped_column(Float, nullable=False)
    spread_mean: Mapped[float] = mapped_column(Float, nullable=False)
    spread_std: Mapped[float] = mapped_column(Float, nullable=False)
    zscore: Mapped[float] = mapped_column(Float, nullable=False)
    correlation: Mapped[float] = mapped_column(Float, nullable=False)
    vol_left: Mapped[float] = mapped_column(Float, default=0.0)
    vol_right: Mapped[float] = mapped_column(Float, default=0.0)
    regime: Mapped[str] = mapped_column(String(20), default="unknown")

    __table_args__ = (
        Index("ix_features_ts", "timestamp_ms"),
    )


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    qty_requested: Mapped[float] = mapped_column(Float, nullable=False)
    qty_filled: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    leg: Mapped[str] = mapped_column(String(10), default="")
    level: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_orders_cycle", "cycle_id"),
        Index("ix_orders_status", "status"),
    )


class CycleRecord(Base):
    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    opened_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_at_ms: Mapped[int] = mapped_column(Integer, default=0)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    max_level_filled: Mapped[int] = mapped_column(Integer, default=0)
    entry_z_avg: Mapped[float] = mapped_column(Float, default=0.0)
    exit_z: Mapped[float] = mapped_column(Float, default=0.0)
    peak_adverse_z: Mapped[float] = mapped_column(Float, default=0.0)
    peak_favorable_z: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    total_fees: Mapped[float] = mapped_column(Float, default=0.0)
    total_slippage: Mapped[float] = mapped_column(Float, default=0.0)
    stop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_cycles_opened", "opened_at_ms"),
    )


class IncidentRecord(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    cycle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class StateSnapshot(Base):
    __tablename__ = "state_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
