"""Tests for the multi-asset replay engine."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hyperoil.donchian.backtest.multi_replay import MultiAssetReplayEngine
from hyperoil.donchian.config import AssetConfig
from hyperoil.donchian.types import AssetClass

INTERVAL_MS = 4 * 60 * 60 * 1000


def _make_parquet(tmp_path: Path, prefix: str, ticker: str, ts: list[int],
                  closes: list[float]) -> None:
    df = pd.DataFrame({
        "timestamp_ms": ts,
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [100.0] * len(ts),
        "symbol": [f"{prefix}:{ticker}"] * len(ts),
        "source": ["test"] * len(ts),
    })
    path = tmp_path / f"{prefix}_{ticker}.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)


def _asset(ticker: str, prefix: str = "xyz", needs_ffill: bool = False) -> AssetConfig:
    return AssetConfig(
        symbol=ticker, hl_ticker=ticker, dex_prefix=prefix,
        asset_class=AssetClass.CRYPTO_MAJOR, needs_ffill=needs_ffill,
    )


def test_two_assets_aligned_grid(tmp_path):
    ts = [INTERVAL_MS * i for i in range(10)]
    _make_parquet(tmp_path, "xyz", "A", ts, [100.0 + i for i in range(10)])
    _make_parquet(tmp_path, "xyz", "B", ts, [50.0 + i for i in range(10)])

    eng = MultiAssetReplayEngine(str(tmp_path), [_asset("A"), _asset("B")])
    bars = list(eng.iter_bars())
    assert len(bars) == 10
    assert bars[0].timestamp_ms == ts[0]
    assert "xyz:A" in bars[0].bars and "xyz:B" in bars[0].bars
    assert bars[5].bars["xyz:A"].close == 105.0


def test_misaligned_assets_outer_join(tmp_path):
    """One asset starts later — cold-start rows should be dropped."""
    ts_full = [INTERVAL_MS * i for i in range(10)]
    ts_late = ts_full[3:]
    _make_parquet(tmp_path, "xyz", "A", ts_full, [100.0] * 10)
    _make_parquet(tmp_path, "xyz", "B", ts_late, [50.0] * 7)

    eng = MultiAssetReplayEngine(str(tmp_path), [_asset("A"), _asset("B")])
    bars = list(eng.iter_bars())
    # Cold-start rows where B is NaN must be dropped → 7 rows
    assert len(bars) == 7
    assert bars[0].timestamp_ms == ts_late[0]


def test_ffill_marks_filled_bars(tmp_path):
    """An asset with needs_ffill=True should ffill internal gaps and mark them."""
    ts_full = [INTERVAL_MS * i for i in range(10)]
    # Asset B is missing every other bar
    ts_sparse = [INTERVAL_MS * i for i in [0, 2, 4, 6, 8]]
    _make_parquet(tmp_path, "xyz", "A", ts_full, [100.0] * 10)
    _make_parquet(tmp_path, "xyz", "B", ts_sparse, [50.0, 51.0, 52.0, 53.0, 54.0])

    eng = MultiAssetReplayEngine(
        str(tmp_path),
        [_asset("A"), _asset("B", needs_ffill=True)],
    )
    bars = list(eng.iter_bars())
    assert len(bars) == 10
    # Bar 1 had no real B data → ffilled from bar 0
    assert bars[1].bars["xyz:B"].close == 50.0
    assert bars[1].bars["xyz:B"].is_filled is True
    assert bars[2].bars["xyz:B"].is_filled is False


def test_start_end_clip(tmp_path):
    ts = [INTERVAL_MS * i for i in range(20)]
    _make_parquet(tmp_path, "xyz", "A", ts, [100.0] * 20)

    eng = MultiAssetReplayEngine(
        str(tmp_path), [_asset("A")],
        start_ms=INTERVAL_MS * 5, end_ms=INTERVAL_MS * 10,
    )
    bars = list(eng.iter_bars())
    assert len(bars) == 6  # ts[5] .. ts[10] inclusive
    assert bars[0].timestamp_ms == INTERVAL_MS * 5
    assert bars[-1].timestamp_ms == INTERVAL_MS * 10


def test_missing_parquet_raises(tmp_path):
    eng = MultiAssetReplayEngine(str(tmp_path), [_asset("A")])
    with pytest.raises(FileNotFoundError):
        eng.load()


def test_empty_assets_raises(tmp_path):
    with pytest.raises(ValueError, match="assets"):
        MultiAssetReplayEngine(str(tmp_path), [])
