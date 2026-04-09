"""Donchian channel computation: upper, lower, mid for one lookback window.

The breakout signal asks "did the current close break above the previous N-bar
high?" — so the channel is computed over the N bars STRICTLY BEFORE the current
bar. Including the current bar would make the breakout condition tautological
(close <= high <= upper, always).

Mathematical contract:
    Given highs/lows arrays where index -1 is the current (just-closed) bar:
        upper = max(highs[-N-1 : -1])
        lower = min(lows [-N-1 : -1])
        mid   = (upper + lower) / 2
    The current bar's high/low are NOT in the window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Channel:
    """Donchian channel snapshot for one lookback at one point in time."""
    lookback: int
    upper: float
    lower: float
    mid: float


def compute_channel(
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int,
) -> Channel:
    """Compute the Donchian channel for the `lookback` bars preceding the
    current (final) bar in the input arrays.

    Requires at least ``lookback + 1`` elements in both arrays. The current
    bar (index -1) is excluded from the rolling window — see module docstring.
    """
    if lookback <= 0:
        raise ValueError(f"lookback must be positive, got {lookback}")
    if len(highs) != len(lows):
        raise ValueError(f"highs/lows length mismatch: {len(highs)} vs {len(lows)}")
    if len(highs) < lookback + 1:
        raise ValueError(
            f"need at least {lookback + 1} bars to compute lookback={lookback}, "
            f"got {len(highs)}"
        )

    window_highs = highs[-lookback - 1 : -1]
    window_lows = lows[-lookback - 1 : -1]
    upper = float(np.max(window_highs))
    lower = float(np.min(window_lows))
    mid = (upper + lower) / 2.0
    return Channel(lookback=lookback, upper=upper, lower=lower, mid=mid)
