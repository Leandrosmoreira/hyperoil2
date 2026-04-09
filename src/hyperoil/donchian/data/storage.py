"""Storage layer for Donchian historical candles.

Two persistence backends:
1. Parquet — fast columnar files, one per symbol, primary backtest source.
2. SQLite — same DB as main HyperOil v2, used for live warmup and incremental updates.

The Parquet layout is `{parquet_dir}/{safe_symbol}.parquet` where safe_symbol
replaces ':' with '_' (e.g. 'xyz:GOLD' -> 'xyz_GOLD.parquet').
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from hyperoil.donchian.data.models import DonchianCandleRecord
from hyperoil.observability.logger import get_logger
from hyperoil.storage.database import get_session

log = get_logger(__name__)


@dataclass(frozen=True)
class ValidationReport:
    """Result of validating a candle DataFrame."""
    symbol: str
    n_rows: int
    nan_pct: float
    max_gap_hours: float
    min_ts: int
    max_ts: int
    valid: bool
    errors: list[str]


def safe_symbol(symbol: str) -> str:
    """Convert 'xyz:GOLD' -> 'xyz_GOLD' for filesystem-safe filenames."""
    return symbol.replace(":", "_").replace("/", "_")


def parquet_path(parquet_dir: str, symbol: str) -> Path:
    return Path(parquet_dir) / f"{safe_symbol(symbol)}.parquet"


def write_parquet(parquet_dir: str, symbol: str, df: pd.DataFrame) -> Path:
    """Write candles DataFrame to Parquet, sorted by timestamp.

    Required columns: timestamp_ms (int64), open, high, low, close, volume (float).
    Symbol and source are added if missing.
    """
    path = parquet_path(parquet_dir, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)

    df = df.copy()
    # Defensive: collapse any duplicate columns before writing.
    df = df.loc[:, ~df.columns.duplicated()]
    if "symbol" not in df.columns:
        df["symbol"] = symbol
    if "source" not in df.columns:
        df["source"] = "unknown"

    df = df.sort_values("timestamp_ms").drop_duplicates("timestamp_ms", keep="last")
    df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
    log.info("parquet_written", symbol=symbol, path=str(path), rows=len(df))
    return path


def read_parquet(parquet_dir: str, symbol: str) -> pd.DataFrame:
    """Read candles for a symbol from Parquet. Returns empty df if file missing."""
    path = parquet_path(parquet_dir, symbol)
    if not path.exists():
        return pd.DataFrame(
            columns=["timestamp_ms", "open", "high", "low", "close", "volume", "symbol", "source"]
        )
    return pd.read_parquet(path, engine="pyarrow")


def validate_candles(
    symbol: str,
    df: pd.DataFrame,
    interval_hours: float = 4.0,
    min_rows: int = 3000,
    max_nan_pct: float = 0.05,
    max_gap_factor: float = 6.0,
) -> ValidationReport:
    """Run quality checks on a candles DataFrame.

    Checks:
    - Minimum row count (default 3000 = ~500 days at 4h)
    - NaN percentage in OHLC columns (default <5%)
    - Max gap between consecutive timestamps (default <6x interval = 24h for 4h candles)
    """
    errors: list[str] = []
    n_rows = len(df)

    if n_rows == 0:
        return ValidationReport(symbol, 0, 0.0, 0.0, 0, 0, False, ["empty dataframe"])

    if n_rows < min_rows:
        errors.append(f"too few rows: {n_rows} < {min_rows}")

    ohlc_cols = ["open", "high", "low", "close"]
    nan_count = df[ohlc_cols].isna().sum().sum()
    nan_pct = nan_count / (n_rows * len(ohlc_cols))
    if nan_pct > max_nan_pct:
        errors.append(f"nan_pct {nan_pct:.3f} > {max_nan_pct}")

    ts_sorted = df["timestamp_ms"].sort_values().to_numpy()
    if len(ts_sorted) > 1:
        diffs_ms = ts_sorted[1:] - ts_sorted[:-1]
        max_gap_ms = int(diffs_ms.max())
        max_gap_hours = max_gap_ms / 1000.0 / 3600.0
        max_allowed_hours = interval_hours * max_gap_factor
        if max_gap_hours > max_allowed_hours:
            errors.append(f"max_gap_hours {max_gap_hours:.1f} > {max_allowed_hours:.1f}")
    else:
        max_gap_hours = 0.0

    return ValidationReport(
        symbol=symbol,
        n_rows=n_rows,
        nan_pct=float(nan_pct),
        max_gap_hours=float(max_gap_hours),
        min_ts=int(ts_sorted[0]),
        max_ts=int(ts_sorted[-1]),
        valid=len(errors) == 0,
        errors=errors,
    )


async def upsert_candles_to_db(symbol: str, interval: str, df: pd.DataFrame) -> int:
    """Insert candles into SQLite, ignoring duplicates (idempotent).

    Uses sqlite ON CONFLICT to skip rows that already exist.
    Returns number of rows attempted (not necessarily inserted).
    """
    if df.empty:
        return 0

    rows = [
        {
            "symbol": symbol,
            "interval": interval,
            "timestamp_ms": int(row.timestamp_ms),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume) if not pd.isna(row.volume) else 0.0,
            "source": str(getattr(row, "source", "unknown")),
        }
        for row in df.itertuples(index=False)
    ]

    # SQLite has a hard limit of ~32k bound parameters per statement. With 9
    # columns, that caps us at ~3500 rows/batch. Use 2000 for safety margin.
    BATCH = 2000
    async with get_session() as session:
        for i in range(0, len(rows), BATCH):
            chunk = rows[i : i + BATCH]
            stmt = sqlite_insert(DonchianCandleRecord).values(chunk)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["symbol", "interval", "timestamp_ms"]
            )
            await session.execute(stmt)
        await session.commit()

    log.info("candles_upserted", symbol=symbol, interval=interval, count=len(rows))
    return len(rows)


async def load_candles_from_db(
    symbol: str,
    interval: str,
    limit: int | None = None,
) -> pd.DataFrame:
    """Load candles for one symbol from SQLite, ordered by timestamp ascending."""
    async with get_session() as session:
        stmt = (
            select(
                DonchianCandleRecord.timestamp_ms,
                DonchianCandleRecord.open,
                DonchianCandleRecord.high,
                DonchianCandleRecord.low,
                DonchianCandleRecord.close,
                DonchianCandleRecord.volume,
                DonchianCandleRecord.source,
            )
            .where(
                DonchianCandleRecord.symbol == symbol,
                DonchianCandleRecord.interval == interval,
            )
            .order_by(DonchianCandleRecord.timestamp_ms.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        rows = result.all()

    if not rows:
        return pd.DataFrame(
            columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
        )

    df = pd.DataFrame(rows, columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"])
    df["symbol"] = symbol
    return df.sort_values("timestamp_ms").reset_index(drop=True)
