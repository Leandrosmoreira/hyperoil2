"""Unit tests for the Donchian position sizer (score tier + caps)."""

from __future__ import annotations

import math

import pytest

from hyperoil.donchian.config import DonchianRiskConfig, DonchianSizingConfig
from hyperoil.donchian.sizing.position_sizer import (
    DonchianPositionSizer,
    compute_drawdown_cap,
    compute_portfolio_targets,
    compute_score_tier,
)
from hyperoil.donchian.sizing.risk_parity import RiskParityEngine
from hyperoil.donchian.sizing.vol_target import VolatilityTargetEngine
from hyperoil.donchian.types import AssetClass


# ----------------------------------------------------------------------
# compute_score_tier
# ----------------------------------------------------------------------
THRESHOLDS = {
    "half_pos": 0.33,
    "full_pos": 0.55,
    "lever_1_5x": 0.55,
    "lever_2x": 0.70,
    "lever_3x": 0.85,
}


def test_score_below_half_no_position():
    assert compute_score_tier(0.0, THRESHOLDS) == (0.0, 0.0)
    assert compute_score_tier(0.32, THRESHOLDS) == (0.0, 0.0)


def test_score_half_position():
    assert compute_score_tier(0.33, THRESHOLDS) == (0.5, 1.0)
    assert compute_score_tier(0.54, THRESHOLDS) == (0.5, 1.0)


def test_score_full_position_low_lev():
    assert compute_score_tier(0.55, THRESHOLDS) == (1.0, 1.5)
    assert compute_score_tier(0.69, THRESHOLDS) == (1.0, 1.5)


def test_score_lev_2x():
    assert compute_score_tier(0.70, THRESHOLDS) == (1.0, 2.0)
    assert compute_score_tier(0.84, THRESHOLDS) == (1.0, 2.0)


def test_score_lev_3x():
    assert compute_score_tier(0.85, THRESHOLDS) == (1.0, 3.0)
    assert compute_score_tier(1.0, THRESHOLDS) == (1.0, 3.0)


# ----------------------------------------------------------------------
# compute_drawdown_cap
# ----------------------------------------------------------------------
def _risk():
    return DonchianRiskConfig()


def test_dd_below_10_no_cap():
    assert compute_drawdown_cap(0.05, _risk()) == math.inf


def test_dd_10_to_15_caps_1_5():
    assert compute_drawdown_cap(0.10, _risk()) == 1.5
    assert compute_drawdown_cap(0.149, _risk()) == 1.5


def test_dd_15_to_20_caps_1():
    assert compute_drawdown_cap(0.15, _risk()) == 1.0
    assert compute_drawdown_cap(0.199, _risk()) == 1.0


def test_dd_20_or_more_shutdown():
    assert compute_drawdown_cap(0.20, _risk()) == 0.0
    assert compute_drawdown_cap(0.50, _risk()) == 0.0


# ----------------------------------------------------------------------
# DonchianPositionSizer
# ----------------------------------------------------------------------
def _sizer():
    return DonchianPositionSizer(
        sizing_cfg=DonchianSizingConfig(),
        risk_cfg=DonchianRiskConfig(),
    )


def test_score_too_low_returns_zero():
    s = _sizer()
    res = s.size_position(
        symbol="BTC", capital=10000, weight=0.04, vol_factor=1.0,
        score=0.20, asset_class=AssetClass.CRYPTO_MAJOR,
        api_max_leverage=50, drawdown_pct=0.0,
    )
    assert res.target_notional_usd == 0.0
    assert res.cap_applied == "score"


def test_drawdown_above_max_shuts_down():
    """CRITICAL invariant from the plan: dd > 20% → 0 positions."""
    s = _sizer()
    res = s.size_position(
        symbol="BTC", capital=10000, weight=0.04, vol_factor=1.0,
        score=1.0, asset_class=AssetClass.CRYPTO_MAJOR,
        api_max_leverage=50, drawdown_pct=0.21,
    )
    assert res.target_notional_usd == 0.0
    assert res.cap_applied == "dd"


def test_basic_full_position_no_caps_binding():
    """Score 0.6 → (1.0, 1.5x). Class cap for crypto_major = 2.0, api 50.
    Binding cap should be 'score' (1.5)."""
    s = _sizer()
    res = s.size_position(
        symbol="BTC", capital=10000, weight=0.04, vol_factor=1.0,
        score=0.6, asset_class=AssetClass.CRYPTO_MAJOR,
        api_max_leverage=50, drawdown_pct=0.0,
    )
    expected = 10000 * 0.04 * 1.0 * 1.0 * 1.5  # = 600
    assert res.target_notional_usd == pytest.approx(expected)
    assert res.leverage_used == 1.5
    assert res.cap_applied == "score"


def test_class_cap_binds():
    """Score 0.9 → score_lev = 3.0 ; CRYPTO_MAJOR class cap = 2.0 ; api 50.
    Binding should be 'class' (2.0)."""
    s = _sizer()
    res = s.size_position(
        symbol="BTC", capital=10000, weight=0.04, vol_factor=1.0,
        score=0.9, asset_class=AssetClass.CRYPTO_MAJOR,
        api_max_leverage=50, drawdown_pct=0.0,
    )
    assert res.leverage_used == 2.0
    assert res.cap_applied == "class"
    expected = 10000 * 0.04 * 1.0 * 1.0 * 2.0  # = 800
    assert res.target_notional_usd == pytest.approx(expected)


def test_api_cap_binds():
    """Force api_max_leverage to be the smallest cap."""
    s = _sizer()
    res = s.size_position(
        symbol="HYPE", capital=10000, weight=0.04, vol_factor=1.0,
        score=0.9, asset_class=AssetClass.COMMODITY,  # class cap 3.0
        api_max_leverage=1.2, drawdown_pct=0.0,
    )
    assert res.leverage_used == 1.2
    assert res.cap_applied == "api"


def test_dd_cap_binds():
    """dd = 12% → cap 1.5x ; score 0.9 would give 3.0. Binding = dd."""
    s = _sizer()
    res = s.size_position(
        symbol="BTC", capital=10000, weight=0.04, vol_factor=1.0,
        score=0.9, asset_class=AssetClass.COMMODITY,  # class cap 3.0
        api_max_leverage=50, drawdown_pct=0.12,
    )
    assert res.leverage_used == 1.5
    assert res.cap_applied == "dd"


def test_max_position_pct_clip():
    """A huge weight × vol_factor combination must clip to capital * max_position_pct."""
    s = _sizer()
    res = s.size_position(
        symbol="BTC", capital=10000, weight=0.99, vol_factor=3.0,
        score=0.9, asset_class=AssetClass.COMMODITY,  # cap 3.0
        api_max_leverage=50, drawdown_pct=0.0,
    )
    # raw = 10000 * 0.99 * 3.0 * 1.0 * 3.0 = 89100 ; capped at 4000 (max_position_pct=0.4)
    assert res.target_notional_usd == pytest.approx(10000 * 0.4)
    assert res.cap_applied == "max_pos_pct"


def test_half_position_score():
    s = _sizer()
    res = s.size_position(
        symbol="BTC", capital=10000, weight=0.04, vol_factor=1.0,
        score=0.4, asset_class=AssetClass.CRYPTO_MAJOR,
        api_max_leverage=50, drawdown_pct=0.0,
    )
    # 0.5 sizing × 1.0 lev → 10000 * 0.04 * 1.0 * 0.5 * 1.0 = 200
    assert res.target_notional_usd == pytest.approx(200)
    assert res.sizing_factor == 0.5
    assert res.leverage_used == 1.0


# ----------------------------------------------------------------------
# compute_portfolio_targets — end-to-end
# ----------------------------------------------------------------------
def test_portfolio_targets_dd_shutdown_zeros_everything():
    """CRITICAL plan invariant: DD > 20% → 0 positions across all assets."""
    rp = RiskParityEngine()
    vt = VolatilityTargetEngine(vol_target_annual=0.25, vol_factor_cap=3.0)
    sz = _sizer()
    vols = {"BTC": 0.6, "ETH": 0.7, "GOLD": 0.15, "EUR": 0.07}
    scores = {"BTC": 1.0, "ETH": 0.9, "GOLD": 0.8, "EUR": 0.6}
    classes = {
        "BTC": AssetClass.CRYPTO_MAJOR,
        "ETH": AssetClass.CRYPTO_MAJOR,
        "GOLD": AssetClass.COMMODITY,
        "EUR": AssetClass.FOREX,
    }
    targets = compute_portfolio_targets(
        vols=vols, scores=scores, asset_classes=classes,
        api_max_leverage={s: 10.0 for s in vols},
        capital=10000, drawdown_pct=0.25,
        risk_parity=rp, vol_target=vt, sizer=sz,
    )
    assert all(t.target_notional_usd == 0.0 for t in targets.values())
    assert all(t.cap_applied == "dd" for t in targets.values())


def test_portfolio_targets_weights_sum_check():
    """Even after sizing, the (weight) field on each result must sum to ~1.0
    for the universe with valid vols (informational invariant from RP)."""
    rp = RiskParityEngine()
    vt = VolatilityTargetEngine(vol_target_annual=0.25)
    sz = _sizer()
    vols = {"BTC": 0.6, "ETH": 0.7, "GOLD": 0.15, "EUR": 0.07}
    scores = {"BTC": 0.9, "ETH": 0.9, "GOLD": 0.9, "EUR": 0.9}
    classes = {
        "BTC": AssetClass.CRYPTO_MAJOR,
        "ETH": AssetClass.CRYPTO_MAJOR,
        "GOLD": AssetClass.COMMODITY,
        "EUR": AssetClass.FOREX,
    }
    targets = compute_portfolio_targets(
        vols=vols, scores=scores, asset_classes=classes,
        api_max_leverage={s: 10.0 for s in vols},
        capital=10000, drawdown_pct=0.0,
        risk_parity=rp, vol_target=vt, sizer=sz,
    )
    total_weight = sum(t.weight for t in targets.values())
    assert total_weight == pytest.approx(1.0, abs=1e-12)
    # And every target is positive
    assert all(t.target_notional_usd > 0 for t in targets.values())


def test_portfolio_targets_degenerate_vol_zero_target():
    rp = RiskParityEngine()
    vt = VolatilityTargetEngine(vol_target_annual=0.25)
    sz = _sizer()
    targets = compute_portfolio_targets(
        vols={"BTC": 0.5, "DEAD": 0.0},
        scores={"BTC": 0.8, "DEAD": 0.8},
        asset_classes={"BTC": AssetClass.CRYPTO_MAJOR, "DEAD": AssetClass.CRYPTO_MINOR},
        api_max_leverage={"BTC": 10.0, "DEAD": 10.0},
        capital=10000, drawdown_pct=0.0,
        risk_parity=rp, vol_target=vt, sizer=sz,
    )
    assert targets["DEAD"].target_notional_usd == 0.0
    assert targets["DEAD"].cap_applied == "vol"
    assert targets["BTC"].target_notional_usd > 0
