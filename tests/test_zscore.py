"""Tests for z-score computation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from hyperoil.signals.zscore import compute_zscore, zscore_single


class TestComputeZscore:
    def test_basic(self) -> None:
        spread = pd.Series(np.random.RandomState(42).normal(0, 1, 200))
        result = compute_zscore(spread, window=50)
        assert "zscore" in result.columns
        assert "spread_mean" in result.columns
        assert "spread_std" in result.columns
        # After warmup, z-score should be finite
        valid = result["zscore"].dropna()
        assert len(valid) > 100
        assert np.isfinite(valid).all()

    def test_zscore_near_zero_for_stationary(self) -> None:
        # Stationary series — z-score should hover around 0
        rng = np.random.RandomState(42)
        spread = pd.Series(rng.normal(0, 1, 500))
        result = compute_zscore(spread, window=100)
        valid = result["zscore"].dropna()
        assert abs(valid.mean()) < 0.5

    def test_min_std_prevents_explosion(self) -> None:
        # Constant spread — std = 0 without protection
        spread = pd.Series([5.0] * 200)
        result = compute_zscore(spread, window=50, min_std=0.001)
        valid = result["zscore"].dropna()
        assert np.isfinite(valid).all()
        # z-score should be 0 since spread == mean
        assert abs(valid.iloc[-1]) < 0.01

    def test_trending_series_has_large_z(self) -> None:
        # Trending series — should have large z-score at end
        spread = pd.Series(np.linspace(0, 10, 200))
        result = compute_zscore(spread, window=50)
        # Last z-score should be positive and large
        last_z = result["zscore"].iloc[-1]
        assert last_z > 1.0


class TestZscoreSingle:
    def test_basic(self) -> None:
        z = zscore_single(current_spread=1.5, mean=0.0, std=1.0)
        assert z == 1.5

    def test_min_std(self) -> None:
        z = zscore_single(current_spread=0.001, mean=0.0, std=0.0, min_std=0.001)
        assert z == 1.0

    def test_negative(self) -> None:
        z = zscore_single(current_spread=-2.0, mean=0.0, std=1.0)
        assert z == -2.0
