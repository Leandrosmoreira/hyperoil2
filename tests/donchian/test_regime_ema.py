"""Unit tests for the EMA regime filter."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hyperoil.donchian.signals.regime_ema import compute_ema, entry_allowed


def test_ema_constant_input_converges_to_constant():
    values = np.full(100, 7.5)
    ema = compute_ema(values, period=20)
    assert ema == pytest.approx(7.5, rel=1e-9)


def test_ema_matches_pandas_recursive():
    """compute_ema must match pandas ewm(span=, adjust=False).iloc[-1]."""
    rng = np.random.default_rng(42)
    values = rng.normal(loc=100, scale=5, size=500)
    period = 50
    expected = float(
        pd.Series(values).ewm(span=period, adjust=False).mean().iloc[-1]
    )
    actual = compute_ema(values, period=period)
    assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12)


def test_ema_responds_to_step_change():
    """A step from 10 → 20 should pull the EMA upward over time."""
    values = np.concatenate([np.full(200, 10.0), np.full(200, 20.0)])
    ema = compute_ema(values, period=20)
    # After 200 bars at 20.0 (10x the period) the EMA is essentially at 20.
    assert ema == pytest.approx(20.0, abs=0.001)


def test_entry_allowed_both_conditions():
    # Both true → True
    assert entry_allowed(close=110.0, ema=100.0, score=0.5, min_score=0.33) is True
    # Below EMA → False
    assert entry_allowed(close=90.0, ema=100.0, score=0.5, min_score=0.33) is False
    # Score too low → False
    assert entry_allowed(close=110.0, ema=100.0, score=0.20, min_score=0.33) is False
    # Edge: close exactly at EMA is NOT above → False
    assert entry_allowed(close=100.0, ema=100.0, score=0.5, min_score=0.33) is False
    # Edge: score exactly at threshold is allowed (>=)
    assert entry_allowed(close=110.0, ema=100.0, score=0.33, min_score=0.33) is True


def test_empty_ema_raises():
    with pytest.raises(ValueError, match="empty"):
        compute_ema(np.array([]), period=10)


def test_invalid_period_raises():
    with pytest.raises(ValueError, match="period must be positive"):
        compute_ema(np.array([1.0, 2.0]), period=0)
