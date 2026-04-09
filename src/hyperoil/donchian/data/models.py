"""SQLAlchemy ORM model for Donchian candles.

Reuses the same Base as the main HyperOil v2 storage so all tables live
in the same SQLite database (data/hyperoil.db). Donchian candles are
stored in a separate table (`donchian_candles`) keyed by (symbol, interval, timestamp_ms).
"""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hyperoil.storage.models import Base


class DonchianCandleRecord(Base):
    """One OHLCV candle for one symbol at one timestamp.

    Symbol format: 'xyz:GOLD', 'hyna:BTC', etc. (matches Hyperliquid HIP-3 naming).
    Source tracks which provider supplied the data: 'yfinance', 'binance', 'hyperliquid'.
    """

    __tablename__ = "donchian_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")

    __table_args__ = (
        # One candle per (symbol, interval, timestamp) — idempotent inserts
        UniqueConstraint("symbol", "interval", "timestamp_ms", name="uq_donchian_candle"),
        Index("ix_donchian_candles_sym_int_ts", "symbol", "interval", "timestamp_ms"),
    )
