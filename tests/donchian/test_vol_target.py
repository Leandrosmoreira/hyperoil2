"""Unit tests for the volatility-targeting engine."""

from __future__ import annotations

import pytest

from hyperoil.donchian.sizing.vol_target import VolatilityTargetEngine


def test_factor_at_target_vol_is_one():
    """If asset vol == vol_target_annual, factor = 1.0."""
    eng = VolatilityTargetEngine(vol_target_annual=0.25, vol_factor_cap=3.0)
    assert eng.factor(0.25) == pytest.approx(1.0)


def test_factor_low_vol_capped():
    """An asset with vol much smaller than target → cap at vol_factor_cap."""
    eng = VolatilityTargetEngine(vol_target_annual=0.25, vol_factor_cap=3.0)
    # raw = 0.25/0.01 = 25 → cap 3.0
    assert eng.factor(0.01) == 3.0


def test_factor_high_vol_scales_down():
    """High-vol asset → factor < 1, never capped from below."""
    eng = VolatilityTargetEngine(vol_target_annual=0.25)
    # vol=0.50 → factor = 0.25/0.50 = 0.5
    assert eng.factor(0.50) == pytest.approx(0.5)


def test_factor_zero_vol_returns_zero():
    eng = VolatilityTargetEngine(vol_target_annual=0.25)
    assert eng.factor(0.0) == 0.0


def test_factor_nan_and_inf_return_zero():
    eng = VolatilityTargetEngine(vol_target_annual=0.25)
    assert eng.factor(float("nan")) == 0.0
    assert eng.factor(float("inf")) == 0.0
    assert eng.factor(-0.1) == 0.0


def test_factors_dict_apply():
    eng = VolatilityTargetEngine(vol_target_annual=0.25, vol_factor_cap=3.0)
    out = eng.factors({"BTC": 0.6, "GOLD": 0.15, "DEAD": 0.0, "EUR": 0.07})
    # 0.25/0.6 ≈ 0.4167 ; 0.25/0.15 ≈ 1.6667 ; 0.25/0.07 ≈ 3.571 → cap 3.0
    assert out["BTC"] == pytest.approx(0.41666, rel=1e-4)
    assert out["GOLD"] == pytest.approx(1.66666, rel=1e-4)
    assert out["EUR"] == 3.0  # capped
    assert out["DEAD"] == 0.0


def test_invalid_construction_raises():
    with pytest.raises(ValueError, match="vol_target_annual"):
        VolatilityTargetEngine(vol_target_annual=0.0)
    with pytest.raises(ValueError, match="vol_factor_cap"):
        VolatilityTargetEngine(vol_target_annual=0.25, vol_factor_cap=-1.0)
