"""Unit tests for the Donchian ensemble score."""

from __future__ import annotations

import numpy as np
import pytest

from hyperoil.donchian.signals.ensemble import compute_ensemble


def _flat_then_spike(n_flat: int, spike_close: float):
    """Build OHLC arrays with `n_flat` flat bars then one current bar at `spike_close`."""
    highs = np.array([10.0] * n_flat + [spike_close + 1.0])
    lows = np.array([5.0] * n_flat + [spike_close - 1.0])
    closes = np.array([8.0] * n_flat + [spike_close])
    return highs, lows, closes


def test_score_one_when_breaking_all_uppers():
    """Spike close above the highest upper across all lookbacks → score=1.0."""
    highs, lows, closes = _flat_then_spike(n_flat=200, spike_close=50.0)
    res = compute_ensemble(highs, lows, closes, lookbacks=[5, 10, 20, 50])
    assert res.score == 1.0
    assert all(res.breakouts)
    # Dominant = LARGEST lookback that broke out
    assert res.dominant_lookback == 50


def test_score_zero_when_no_breakout():
    """Close below all uppers → score=0.0."""
    highs, lows, closes = _flat_then_spike(n_flat=200, spike_close=7.0)
    res = compute_ensemble(highs, lows, closes, lookbacks=[5, 10, 20, 50])
    assert res.score == 0.0
    assert not any(res.breakouts)
    assert res.dominant_lookback == 0


def test_partial_breakout_via_rising_window():
    """A monotonically rising window: only the SHORTER lookbacks break out
    because the longer lookbacks include earlier higher highs… wait, the
    opposite: a rising series means longer lookbacks have LOWER prior highs
    (further in the past), so longer lookbacks ARE broken too. Test the
    opposite: a recently-rising series after an old spike — only the recent
    short windows break, the long window still contains the old spike."""
    n = 300
    bars = np.full(n, 5.0)
    bars[0:50] = 100.0      # ancient spike, much higher than current
    # Recent uptrend: last 30 bars rising 5..6
    bars[-31:-1] = np.linspace(5.0, 6.0, 30)
    highs = bars + 0.1
    lows = bars - 0.1
    closes = bars.copy()
    closes[-1] = 6.5  # current close: above recent range, below ancient

    res = compute_ensemble(highs, lows, closes, lookbacks=[5, 10, 20, 250])
    # Short lookbacks should break out, the 250 lookback (which includes the
    # 100.0 ancient spike) should not.
    assert res.breakouts[0] is True   # 5
    assert res.breakouts[1] is True   # 10
    assert res.breakouts[2] is True   # 20
    assert res.breakouts[3] is False  # 250
    assert res.score == 3 / 4
    assert res.dominant_lookback == 20


def test_dominant_lookback_is_largest_breakout():
    n = 100
    highs = np.full(n + 1, 5.0)
    lows = np.full(n + 1, 4.0)
    closes = np.full(n + 1, 4.5)
    closes[-1] = 10.0  # break everything
    highs[-1] = 11.0
    lows[-1] = 9.0
    res = compute_ensemble(highs, lows, closes, lookbacks=[5, 50, 90])
    assert res.dominant_lookback == 90


def test_lookbacks_sorted_in_output():
    """Channels in result should be sorted ascending regardless of input order."""
    highs, lows, closes = _flat_then_spike(n_flat=200, spike_close=50.0)
    res = compute_ensemble(highs, lows, closes, lookbacks=[50, 5, 20, 10])
    sorted_lbs = [ch.lookback for ch in res.channels]
    assert sorted_lbs == [5, 10, 20, 50]


def test_empty_lookbacks_raises():
    highs = np.array([1.0, 2.0])
    lows = np.array([0.5, 1.0])
    closes = np.array([0.7, 1.5])
    with pytest.raises(ValueError, match="lookbacks must not be empty"):
        compute_ensemble(highs, lows, closes, lookbacks=[])
