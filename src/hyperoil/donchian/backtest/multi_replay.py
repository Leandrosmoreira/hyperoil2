"""Multi-asset replay engine for the Donchian backtest.

Loads one Parquet per asset, aligns them on a common 4h timestamp grid via
outer join, forward-fills any gaps for assets that don't trade 24/7
(stocks/indices/commodities), and yields ``MultiBar`` snapshots one
timestamp at a time.

The aligned grid IS the grid the simulator iterates on — every bar emitted
has a value (possibly forward-filled) for every symbol in the universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd

from hyperoil.donchian.config import AssetConfig
from hyperoil.donchian.data.storage import read_parquet
from hyperoil.donchian.types import AssetClass
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class AssetBar:
    """One bar of one asset on the aligned grid."""
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_filled: bool   # True if forward-filled (asset stale this bar)


@dataclass(frozen=True)
class MultiBar:
    """Aligned bars for all symbols at one timestamp."""
    timestamp_ms: int
    bars: dict[str, AssetBar]


def _dex_symbol(asset: AssetConfig) -> str:
    return f"{asset.dex_prefix}:{asset.hl_ticker}"


class MultiAssetReplayEngine:
    """Loads parquets for an asset universe and yields aligned MultiBars.

    Parameters
    ----------
    parquet_dir : str
        Directory containing the per-asset Parquet files.
    assets : list[AssetConfig]
        The universe to load. Symbols are constructed as ``{dex_prefix}:{hl_ticker}``.
    start_ms / end_ms : int | None
        Inclusive timestamp bounds in milliseconds. ``None`` means open-ended.
    interval_ms : int
        Expected bar interval in milliseconds (default 4h = 14_400_000).
    """

    INTERVAL_4H_MS = 4 * 60 * 60 * 1000

    def __init__(
        self,
        parquet_dir: str,
        assets: list[AssetConfig],
        start_ms: int | None = None,
        end_ms: int | None = None,
        interval_ms: int = INTERVAL_4H_MS,
    ) -> None:
        if not assets:
            raise ValueError("assets must not be empty")
        self.parquet_dir = parquet_dir
        self.assets = assets
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.interval_ms = interval_ms
        self._symbols = [_dex_symbol(a) for a in assets]
        self._needs_ffill = {_dex_symbol(a): a.needs_ffill for a in assets}
        self._aligned: pd.DataFrame | None = None

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    # ------------------------------------------------------------------
    # Loading & alignment
    # ------------------------------------------------------------------
    def load(self) -> pd.DataFrame:
        """Load every parquet, build the aligned grid, return the wide DataFrame.

        The wide frame is indexed by ``timestamp_ms`` (int64) and has a
        MultiIndex on columns: (symbol, field) where field ∈ {open, high,
        low, close, volume, is_filled}.
        """
        if self._aligned is not None:
            return self._aligned

        per_asset: dict[str, pd.DataFrame] = {}
        for asset in self.assets:
            sym = _dex_symbol(asset)
            df = read_parquet(self.parquet_dir, sym)
            if df.empty:
                raise FileNotFoundError(f"No parquet data for {sym} in {self.parquet_dir}")

            df = df[["timestamp_ms", "open", "high", "low", "close", "volume"]].copy()
            df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms")
            df = df.set_index("timestamp_ms")
            per_asset[sym] = df

        # Determine the union grid: every distinct timestamp across all assets,
        # then optionally clipped to the requested window.
        all_ts = sorted(set().union(*(df.index for df in per_asset.values())))
        if self.start_ms is not None:
            all_ts = [ts for ts in all_ts if ts >= self.start_ms]
        if self.end_ms is not None:
            all_ts = [ts for ts in all_ts if ts <= self.end_ms]
        if not all_ts:
            raise ValueError("Empty timestamp grid after applying start/end bounds")

        grid_index = pd.Index(all_ts, name="timestamp_ms", dtype="int64")

        # Build wide frame, one block per asset.
        blocks: list[pd.DataFrame] = []
        for sym, df in per_asset.items():
            reidx = df.reindex(grid_index)
            is_filled = reidx["close"].isna().values

            if self._needs_ffill.get(sym, False):
                reidx = reidx.ffill()
            # For assets that don't need ffill (24/7 crypto), still forward-fill
            # any internal gaps so the simulator never sees NaN — these are rare
            # and represent missing data, not "the market was closed".
            reidx = reidx.ffill()
            reidx["is_filled"] = is_filled
            reidx.columns = pd.MultiIndex.from_product([[sym], reidx.columns])
            blocks.append(reidx)

        wide = pd.concat(blocks, axis=1)

        # Drop any leading rows where ANY asset is still NaN (cold start before
        # all symbols have a real first bar).
        # Use only the close column per symbol for the warm-start mask.
        close_cols = [(s, "close") for s in self._symbols]
        any_nan = wide.loc[:, close_cols].isna().any(axis=1)
        wide = wide.loc[~any_nan]

        if wide.empty:
            raise ValueError("Aligned grid is empty after dropping cold-start rows")

        self._aligned = wide
        log.info(
            "multi_replay_loaded",
            symbols=len(self._symbols),
            rows=len(wide),
            start_ms=int(wide.index[0]),
            end_ms=int(wide.index[-1]),
        )
        return wide

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------
    def iter_bars(self) -> Iterator[MultiBar]:
        wide = self.load()
        symbols = self._symbols
        # Cache numpy views per symbol for speed.
        cols: dict[str, dict[str, np.ndarray]] = {}
        for sym in symbols:
            cols[sym] = {
                "open": wide[(sym, "open")].to_numpy(dtype=float),
                "high": wide[(sym, "high")].to_numpy(dtype=float),
                "low": wide[(sym, "low")].to_numpy(dtype=float),
                "close": wide[(sym, "close")].to_numpy(dtype=float),
                "volume": wide[(sym, "volume")].to_numpy(dtype=float),
                "is_filled": wide[(sym, "is_filled")].to_numpy(dtype=bool),
            }
        ts_arr = wide.index.to_numpy(dtype="int64")

        for i, ts in enumerate(ts_arr):
            bars: dict[str, AssetBar] = {}
            for sym in symbols:
                c = cols[sym]
                bars[sym] = AssetBar(
                    open=float(c["open"][i]),
                    high=float(c["high"][i]),
                    low=float(c["low"][i]),
                    close=float(c["close"][i]),
                    volume=float(c["volume"][i]) if not np.isnan(c["volume"][i]) else 0.0,
                    is_filled=bool(c["is_filled"][i]),
                )
            yield MultiBar(timestamp_ms=int(ts), bars=bars)

    def __len__(self) -> int:
        return len(self.load())
