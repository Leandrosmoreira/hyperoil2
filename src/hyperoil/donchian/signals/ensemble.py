"""Ensemble of Donchian breakouts across multiple lookbacks.

For each lookback N, ``signal_N = 1`` if ``close > upper_N``, else 0. The
ensemble score is the mean of all binary signals: 0.0 (no breakout) to 1.0
(every lookback breaking out simultaneously, strong trend).

The "dominant lookback" is the LARGEST N for which ``signal_N = 1`` — the
longest-trend confirmation. Used to set the trailing stop conservatively
(longer-trend mid is further away, gives the position more room).

This is the long-side ensemble (E1 from the plan). Long-short (E2) and
adaptive weighting (E3) are separate modules.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from hyperoil.donchian.signals.donchian_channel import Channel, compute_channel


@dataclass(frozen=True)
class EnsembleResult:
    score: float                 # mean of binary signals, 0.0 - 1.0
    dominant_lookback: int       # largest N with signal=1; 0 if no breakout
    channels: list[Channel]      # one per requested lookback (sorted asc)
    breakouts: list[bool]        # parallel to channels


def compute_ensemble(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    lookbacks: list[int],
) -> EnsembleResult:
    """Compute the ensemble score for the current (last) bar.

    Channels are computed for every lookback in ``lookbacks`` (sorted ascending
    on output for stable iteration). The current bar's close is compared
    against each upper channel; the score is the mean of those binary
    comparisons.
    """
    if not lookbacks:
        raise ValueError("lookbacks must not be empty")
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise ValueError("highs/lows/closes length mismatch")

    sorted_lookbacks = sorted(lookbacks)
    current_close = float(closes[-1])

    channels: list[Channel] = []
    breakouts: list[bool] = []
    dominant = 0

    for lb in sorted_lookbacks:
        ch = compute_channel(highs, lows, lb)
        channels.append(ch)
        broke = current_close > ch.upper
        breakouts.append(broke)
        if broke and lb > dominant:
            dominant = lb

    score = sum(breakouts) / len(breakouts)
    return EnsembleResult(
        score=score,
        dominant_lookback=dominant,
        channels=channels,
        breakouts=breakouts,
    )
