"""Unit tests for risk parity weighting and realized vol."""

from __future__ import annotations

import math

import numpy as np
import pytest

from hyperoil.donchian.sizing.risk_parity import (
    PERIODS_PER_YEAR_4H,
    RiskParityEngine,
    compute_realized_vol,
)


# ----------------------------------------------------------------------
# compute_realized_vol
# ----------------------------------------------------------------------
def test_realized_vol_constant_series_is_zero():
    closes = np.full(200, 100.0)
    vol = compute_realized_vol(closes, window=180)
    assert vol == 0.0


def test_realized_vol_known_log_returns():
    """Known closes → manual calculation cross-check.

    Closes: 100, 110, 100, 110, 100, 110 → log rets ≈ [0.0953, -0.0953, ...]
    std (ddof=1) of [0.09531, -0.09531, 0.09531, -0.09531, 0.09531]
    = 0.10446 (over 5 returns, 6 closes)
    annualized × sqrt(2190) ≈ 4.886
    """
    closes = np.array([100.0, 110.0, 100.0, 110.0, 100.0, 110.0])
    vol = compute_realized_vol(closes, window=5)
    rets = np.diff(np.log(closes))
    expected = float(np.std(rets, ddof=1) * math.sqrt(PERIODS_PER_YEAR_4H))
    assert vol == pytest.approx(expected, rel=1e-12)


def test_realized_vol_insufficient_data_returns_nan():
    closes = np.array([100.0, 101.0, 102.0])
    vol = compute_realized_vol(closes, window=10)
    assert math.isnan(vol)


def test_realized_vol_negative_close_returns_nan():
    closes = np.array([100.0, 101.0, -50.0, 102.0, 103.0, 104.0])
    vol = compute_realized_vol(closes, window=5)
    assert math.isnan(vol)


def test_realized_vol_invalid_window_raises():
    with pytest.raises(ValueError, match="window must be > 1"):
        compute_realized_vol(np.array([1.0, 2.0]), window=1)


# ----------------------------------------------------------------------
# RiskParityEngine
# ----------------------------------------------------------------------
def test_weights_sum_to_one():
    eng = RiskParityEngine()
    vols = {"BTC": 0.6, "ETH": 0.8, "GOLD": 0.15, "EUR": 0.07}
    w = eng.compute_weights(vols)
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-12)
    assert len(w) == 4


def test_low_vol_gets_bigger_weight():
    eng = RiskParityEngine()
    vols = {"low": 0.1, "high": 1.0}
    w = eng.compute_weights(vols)
    assert w["low"] > w["high"]
    # Specifically: w[low]/w[high] = vol[high]/vol[low] = 10
    assert w["low"] / w["high"] == pytest.approx(10.0)


def test_inverse_proportional_relation():
    """w[i] / w[j] = vol[j] / vol[i] — the defining property."""
    eng = RiskParityEngine()
    vols = {"a": 0.2, "b": 0.4, "c": 0.8}
    w = eng.compute_weights(vols)
    assert w["a"] / w["b"] == pytest.approx(vols["b"] / vols["a"])
    assert w["b"] / w["c"] == pytest.approx(vols["c"] / vols["b"])


def test_single_asset_gets_full_weight():
    eng = RiskParityEngine()
    w = eng.compute_weights({"only": 0.5})
    assert w == {"only": 1.0}


def test_zero_vol_is_filtered():
    eng = RiskParityEngine()
    w = eng.compute_weights({"BTC": 0.5, "DEAD": 0.0, "ETH": 0.7})
    assert "DEAD" not in w
    assert sum(w.values()) == pytest.approx(1.0)
    assert set(w.keys()) == {"BTC", "ETH"}


def test_negative_and_nan_vol_are_filtered():
    eng = RiskParityEngine()
    w = eng.compute_weights({"BTC": 0.5, "BAD1": -0.1, "BAD2": float("nan"),
                              "BAD3": float("inf"), "ETH": 0.7})
    assert set(w.keys()) == {"BTC", "ETH"}
    assert sum(w.values()) == pytest.approx(1.0)


def test_all_degenerate_returns_empty():
    eng = RiskParityEngine()
    w = eng.compute_weights({"a": 0.0, "b": -1.0, "c": float("nan")})
    assert w == {}


def test_empty_input_returns_empty():
    assert RiskParityEngine().compute_weights({}) == {}
