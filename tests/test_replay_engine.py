"""Tests for the replay engine."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hyperoil.backtest.replay_engine import ReplayEngine


def _make_df(n: int = 100, start_price: float = 68.0, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic candle data."""
    rng = np.random.default_rng(seed)
    timestamps = list(range(1000, 1000 + n * 900_000, 900_000))  # 15m bars in ms
    prices = start_price + np.cumsum(rng.normal(0, 0.1, n))

    return pd.DataFrame({
        "timestamp_ms": timestamps[:n],
        "open": prices,
        "high": prices + rng.uniform(0, 0.5, n),
        "low": prices - rng.uniform(0, 0.5, n),
        "close": prices + rng.normal(0, 0.05, n),
        "volume": rng.uniform(100, 500, n),
    })


class TestReplayEngine:
    def test_basic_creation(self) -> None:
        df_left = _make_df(50, 68.0, seed=42)
        df_right = _make_df(50, 72.0, seed=43)
        engine = ReplayEngine(df_left, df_right)

        assert engine.total_bars == 50
        assert not engine.is_done

    def test_next_bar(self) -> None:
        df_left = _make_df(10, 68.0, seed=42)
        df_right = _make_df(10, 72.0, seed=43)
        engine = ReplayEngine(df_left, df_right)

        bar = engine.next_bar()
        assert bar is not None
        assert bar.left.close > 0
        assert bar.right.close > 0
        assert engine.current_index == 1

    def test_iterates_all_bars(self) -> None:
        df_left = _make_df(20, 68.0, seed=42)
        df_right = _make_df(20, 72.0, seed=43)
        engine = ReplayEngine(df_left, df_right)

        count = 0
        while not engine.is_done:
            bar = engine.next_bar()
            assert bar is not None
            count += 1

        assert count == 20
        assert engine.is_done
        assert engine.next_bar() is None

    def test_reset(self) -> None:
        df_left = _make_df(10, 68.0, seed=42)
        df_right = _make_df(10, 72.0, seed=43)
        engine = ReplayEngine(df_left, df_right)

        engine.next_bar()
        engine.next_bar()
        assert engine.current_index == 2

        engine.reset()
        assert engine.current_index == 0
        assert not engine.is_done

    def test_iter_bars(self) -> None:
        df_left = _make_df(15, 68.0, seed=42)
        df_right = _make_df(15, 72.0, seed=43)
        engine = ReplayEngine(df_left, df_right)

        bars = engine.iter_bars()
        assert len(bars) == 15
        assert bars[0].timestamp_ms < bars[-1].timestamp_ms

    def test_slice(self) -> None:
        df_left = _make_df(20, 68.0, seed=42)
        df_right = _make_df(20, 72.0, seed=43)
        engine = ReplayEngine(df_left, df_right)

        sliced = engine.slice(5, 10)
        assert len(sliced) == 5

    def test_get_result(self) -> None:
        df_left = _make_df(30, 68.0, seed=42)
        df_right = _make_df(30, 72.0, seed=43)
        engine = ReplayEngine(df_left, df_right)

        for _ in range(10):
            engine.next_bar()

        result = engine.get_result()
        assert result.total_bars == 30
        assert result.bars_processed == 10
        assert result.start_ms > 0

    def test_mismatched_timestamps_inner_join(self) -> None:
        """Only timestamps present in both DataFrames should appear."""
        df_left = _make_df(10, 68.0, seed=42)
        df_right = _make_df(8, 72.0, seed=43)
        # First 8 timestamps match
        engine = ReplayEngine(df_left, df_right)
        assert engine.total_bars == 8

    def test_missing_columns_raises(self) -> None:
        df_left = pd.DataFrame({"timestamp_ms": [1, 2], "close": [68.0, 68.1]})
        df_right = _make_df(2, 72.0, seed=43)

        with pytest.raises(ValueError, match="missing columns"):
            ReplayEngine(df_left, df_right)

    def test_empty_data(self) -> None:
        df_left = _make_df(10, 68.0, seed=42)
        df_right = pd.DataFrame({
            "timestamp_ms": [99999],  # no overlap
            "open": [72.0], "high": [72.5], "low": [71.5],
            "close": [72.1], "volume": [100.0],
        })
        engine = ReplayEngine(df_left, df_right)
        assert engine.total_bars == 0
        assert engine.is_done

    def test_deterministic_replay(self) -> None:
        """Same data produces same bars on two separate runs."""
        df_left = _make_df(20, 68.0, seed=42)
        df_right = _make_df(20, 72.0, seed=43)

        engine1 = ReplayEngine(df_left.copy(), df_right.copy())
        engine2 = ReplayEngine(df_left.copy(), df_right.copy())

        bars1 = engine1.iter_bars()
        bars2 = engine2.iter_bars()

        assert len(bars1) == len(bars2)
        for b1, b2 in zip(bars1, bars2):
            assert b1.timestamp_ms == b2.timestamp_ms
            assert b1.left.close == b2.left.close
            assert b1.right.close == b2.right.close
