"""Unit tests for the Donchian channel primitive."""

from __future__ import annotations

import numpy as np
import pytest

from hyperoil.donchian.signals.donchian_channel import compute_channel


def test_basic_max_min():
    highs = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 99.0])  # last is current bar
    lows = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 88.0])
    ch = compute_channel(highs, lows, lookback=5)
    assert ch.upper == 14.0
    assert ch.lower == 1.0
    assert ch.mid == (14.0 + 1.0) / 2.0
    assert ch.lookback == 5


def test_current_bar_is_excluded_from_window():
    """The CRITICAL contract: the current bar must NOT contribute to upper/lower.
    Otherwise close > upper would be tautologically false (close <= high)."""
    highs = np.array([5.0, 5.0, 5.0, 5.0, 1000.0])  # current bar = huge
    lows = np.array([3.0, 3.0, 3.0, 3.0, 0.001])    # current bar = tiny
    ch = compute_channel(highs, lows, lookback=4)
    # Window is the FIRST 4 bars; current bar (1000/0.001) is excluded
    assert ch.upper == 5.0
    assert ch.lower == 3.0


def test_lookback_smaller_than_buffer():
    """Window slides correctly when buffer is longer than lookback."""
    highs = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    lows = np.array([0.5] * 10)
    ch = compute_channel(highs, lows, lookback=3)
    # Last 4 bars: [..7, 8, 9, 10]; current = 10 excluded; window = [7, 8, 9]
    assert ch.upper == 9.0
    assert ch.lower == 0.5


def test_insufficient_bars_raises():
    highs = np.array([1.0, 2.0, 3.0])
    lows = np.array([0.5, 1.0, 1.5])
    with pytest.raises(ValueError, match="need at least"):
        compute_channel(highs, lows, lookback=5)


def test_exactly_lookback_plus_one_works():
    highs = np.array([1.0, 2.0, 3.0])  # exactly lookback+1 = 3 for lookback=2
    lows = np.array([0.5, 1.0, 1.5])
    ch = compute_channel(highs, lows, lookback=2)
    assert ch.upper == 2.0
    assert ch.lower == 0.5


def test_invalid_lookback_raises():
    highs = np.array([1.0, 2.0, 3.0])
    lows = np.array([0.5, 1.0, 1.5])
    with pytest.raises(ValueError, match="lookback must be positive"):
        compute_channel(highs, lows, lookback=0)


def test_length_mismatch_raises():
    highs = np.array([1.0, 2.0, 3.0])
    lows = np.array([0.5, 1.0])
    with pytest.raises(ValueError, match="length mismatch"):
        compute_channel(highs, lows, lookback=2)
