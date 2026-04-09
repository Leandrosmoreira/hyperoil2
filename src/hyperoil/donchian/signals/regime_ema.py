"""EMA-based regime filter — entries are only allowed when both:

    1. close > EMA(period)        (uptrend confirmation)
    2. ensemble score >= min_score (Donchian breakout confirmation)

The default period (200) follows the paper. The recursive form
(``adjust=False``) is used so the engine can be updated incrementally — only
the previous EMA value needs to be carried, not the full window.
"""

from __future__ import annotations

import numpy as np


def compute_ema(values: np.ndarray, period: int) -> float:
    """Recursive exponential moving average — returns the LAST value only.

    EMA[t] = alpha * x[t] + (1 - alpha) * EMA[t-1]
    EMA[0] = x[0]
    alpha = 2 / (period + 1)

    This matches pandas' ``ewm(span=period, adjust=False).mean().iloc[-1]``.
    For long buffers (>> period) this converges to the true EMA regardless of
    initialization.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(values) == 0:
        raise ValueError("cannot compute EMA on empty array")

    alpha = 2.0 / (period + 1.0)
    ema = float(values[0])
    for v in values[1:]:
        ema = alpha * float(v) + (1.0 - alpha) * ema
    return ema


def entry_allowed(
    close: float,
    ema: float,
    score: float,
    min_score: float,
) -> bool:
    """Long-side entry filter: BOTH conditions must hold."""
    return close > ema and score >= min_score
