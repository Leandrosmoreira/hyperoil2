"""Replay engine — feeds historical candle data bar-by-bar for backtesting.

Deterministic replay: same data + same config = same results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Bar:
    """A single bar (candle) for one symbol."""
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class PairBar:
    """Aligned pair of bars for left and right legs."""
    timestamp_ms: int
    left: Bar
    right: Bar


@dataclass
class ReplayResult:
    """Summary of a replay run."""
    total_bars: int = 0
    start_ms: int = 0
    end_ms: int = 0
    bars_processed: int = 0
    errors: list[str] = field(default_factory=list)


class ReplayEngine:
    """Replays historical data bar-by-bar for deterministic backtesting.

    Accepts two DataFrames (left and right) with columns:
    timestamp_ms, open, high, low, close, volume.
    Aligns them by timestamp and yields PairBars in order.
    """

    def __init__(
        self,
        df_left: pd.DataFrame,
        df_right: pd.DataFrame,
    ) -> None:
        self._bars = self._align_and_build(df_left, df_right)
        self._index = 0

    @property
    def total_bars(self) -> int:
        return len(self._bars)

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def is_done(self) -> bool:
        return self._index >= len(self._bars)

    def reset(self) -> None:
        """Reset replay to the beginning."""
        self._index = 0

    def next_bar(self) -> PairBar | None:
        """Get the next aligned pair bar. Returns None when done."""
        if self._index >= len(self._bars):
            return None
        bar = self._bars[self._index]
        self._index += 1
        return bar

    def iter_bars(self) -> list[PairBar]:
        """Return all bars as a list (for batch processing)."""
        return list(self._bars)

    def slice(self, start: int, end: int) -> list[PairBar]:
        """Get a slice of bars by index."""
        return self._bars[start:end]

    def get_result(self) -> ReplayResult:
        """Get replay summary."""
        if not self._bars:
            return ReplayResult()
        return ReplayResult(
            total_bars=len(self._bars),
            start_ms=self._bars[0].timestamp_ms,
            end_ms=self._bars[-1].timestamp_ms,
            bars_processed=self._index,
        )

    @staticmethod
    def _align_and_build(
        df_left: pd.DataFrame,
        df_right: pd.DataFrame,
    ) -> list[PairBar]:
        """Align two DataFrames by timestamp and build PairBar list."""
        required = {"timestamp_ms", "open", "high", "low", "close", "volume"}

        for name, df in [("left", df_left), ("right", df_right)]:
            missing = required - set(df.columns)
            if missing:
                raise ValueError(f"{name} DataFrame missing columns: {missing}")

        # Inner join on timestamp — only keep aligned bars
        merged = pd.merge(
            df_left, df_right,
            on="timestamp_ms",
            suffixes=("_left", "_right"),
            how="inner",
        ).sort_values("timestamp_ms").reset_index(drop=True)

        if merged.empty:
            log.warning("replay_no_aligned_bars")
            return []

        bars: list[PairBar] = []
        for _, row in merged.iterrows():
            ts = int(row["timestamp_ms"])
            left = Bar(
                timestamp_ms=ts,
                open=float(row["open_left"]),
                high=float(row["high_left"]),
                low=float(row["low_left"]),
                close=float(row["close_left"]),
                volume=float(row["volume_left"]),
            )
            right = Bar(
                timestamp_ms=ts,
                open=float(row["open_right"]),
                high=float(row["high_right"]),
                low=float(row["low_right"]),
                close=float(row["close_right"]),
                volume=float(row["volume_right"]),
            )
            bars.append(PairBar(timestamp_ms=ts, left=left, right=right))

        log.info("replay_loaded", total_bars=len(bars))
        return bars

    @staticmethod
    def from_csv(
        path_left: str,
        path_right: str,
    ) -> "ReplayEngine":
        """Create a ReplayEngine from two CSV files."""
        df_left = pd.read_csv(path_left)
        df_right = pd.read_csv(path_right)
        return ReplayEngine(df_left, df_right)
