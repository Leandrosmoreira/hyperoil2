"""Tests for spread and hedge ratio computations."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hyperoil.signals.spread import (
    compute_hedge_ratio_fixed,
    compute_hedge_ratio_kalman,
    compute_hedge_ratio_rolling,
    compute_hedge_ratio_vol_adjusted,
    compute_spread,
)


def _make_price_pair(n: int = 200, beta: float = 0.95, noise: float = 0.01) -> pd.DataFrame:
    """Generate synthetic correlated price pair."""
    rng = np.random.RandomState(42)
    # Brent as base, CL = beta * Brent + noise
    brent_log = np.cumsum(rng.normal(0, 0.005, n)) + np.log(72.0)
    cl_log = beta * brent_log + rng.normal(0, noise, n) + np.log(68.0) - beta * np.log(72.0)

    return pd.DataFrame({
        "price_left": np.exp(cl_log),
        "price_right": np.exp(brent_log),
    })


class TestHedgeRatioFixed:
    def test_returns_constant_series(self) -> None:
        df = _make_price_pair(200, beta=0.95)
        result = compute_hedge_ratio_fixed(df["price_left"], df["price_right"])
        assert len(result) == 200
        # Should be approximately constant
        assert result.nunique() == 1

    def test_beta_close_to_true(self) -> None:
        df = _make_price_pair(500, beta=0.95, noise=0.002)
        result = compute_hedge_ratio_fixed(df["price_left"], df["price_right"])
        assert abs(result.iloc[0] - 0.95) < 0.15  # within reasonable range

    def test_too_few_data_returns_nan(self) -> None:
        df = _make_price_pair(5, beta=0.95)
        result = compute_hedge_ratio_fixed(df["price_left"], df["price_right"])
        assert result.isna().all()


class TestHedgeRatioRolling:
    def test_first_values_nan(self) -> None:
        df = _make_price_pair(200)
        result = compute_hedge_ratio_rolling(df["price_left"], df["price_right"], window=50)
        assert result.iloc[:49].isna().all()
        assert result.iloc[50:].notna().any()

    def test_values_positive(self) -> None:
        df = _make_price_pair(200)
        result = compute_hedge_ratio_rolling(df["price_left"], df["price_right"], window=50)
        valid = result.dropna()
        assert (valid > 0).all()


class TestHedgeRatioVolAdjusted:
    def test_returns_valid(self) -> None:
        df = _make_price_pair(200)
        ret_left = np.log(df["price_left"]).diff()
        ret_right = np.log(df["price_right"]).diff()
        result = compute_hedge_ratio_vol_adjusted(ret_left, ret_right, window=50)
        valid = result.dropna()
        assert len(valid) > 0
        assert (valid > 0).all()


class TestHedgeRatioKalman:
    def test_adapts_over_time(self) -> None:
        df = _make_price_pair(300, beta=0.95)
        result = compute_hedge_ratio_kalman(df["price_left"], df["price_right"])
        assert result.notna().sum() > 250
        # Should converge toward true beta
        last_50 = result.iloc[-50:].mean()
        assert abs(last_50 - 0.95) < 0.3

    def test_handles_nan(self) -> None:
        df = _make_price_pair(100)
        df.loc[50, "price_left"] = np.nan
        result = compute_hedge_ratio_kalman(df["price_left"], df["price_right"])
        # Should carry forward previous estimate
        assert result.notna().sum() > 90


class TestComputeSpread:
    def test_log_mode(self) -> None:
        df = _make_price_pair(200)
        result = compute_spread(df.copy(), mode="log", hedge_mode="rolling_ols", hedge_window=50)
        assert "hedge_ratio" in result.columns
        assert "spread" in result.columns
        # Spread should be around zero for correlated pair
        valid = result["spread"].dropna()
        assert abs(valid.mean()) < 1.0

    def test_linear_mode(self) -> None:
        df = _make_price_pair(200)
        result = compute_spread(df.copy(), mode="linear", hedge_mode="rolling_ols", hedge_window=50)
        assert "spread" in result.columns

    def test_all_hedge_modes(self) -> None:
        df = _make_price_pair(200)
        for mode in ("fixed", "rolling_ols", "vol_adjusted", "kalman"):
            result = compute_spread(df.copy(), hedge_mode=mode, hedge_window=50)
            assert "hedge_ratio" in result.columns
            assert "spread" in result.columns
