"""Half-life and Hurst exponent for mean reversion analysis.

Ported from v1 — validated math preserved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def halflife_ou(spread: pd.Series) -> float:
    """Estimate half-life via Ornstein-Uhlenbeck regression.

    Regresses spread_diff on spread_lag:
        spread(t) - spread(t-1) = phi * spread(t-1) + epsilon
        half-life = -log(2) / phi
    """
    spread = spread.dropna()
    if len(spread) < 20:
        return np.nan

    lag = spread.shift(1)
    diff = spread - lag
    valid = np.isfinite(lag) & np.isfinite(diff)
    lag_v = lag[valid].values
    diff_v = diff[valid].values

    if len(lag_v) < 10:
        return np.nan

    phi = np.dot(lag_v, diff_v) / np.dot(lag_v, lag_v)

    if phi >= 0:
        return np.nan  # no mean reversion

    return float(-np.log(2) / phi)


def hurst_rs(series: pd.Series, max_k: int = 40) -> float:
    """Hurst exponent via rescaled range (R/S) analysis on increments.

    R/S operates on INCREMENTS (first differences), not levels.
    H < 0.5: mean reverting | H = 0.5: random walk | H > 0.5: trending
    """
    series = series.dropna().values
    increments = np.diff(series)
    n = len(increments)
    if n < 40:
        return np.nan

    rs_list = []
    ns_list = []

    for k in range(2, min(max_k + 1, n // 8)):
        chunk_size = n // k
        if chunk_size < 8:
            break

        rs_vals = []
        for i in range(k):
            chunk = increments[i * chunk_size : (i + 1) * chunk_size]
            mean = np.mean(chunk)
            dev = np.cumsum(chunk - mean)
            r = np.max(dev) - np.min(dev)
            s = np.std(chunk, ddof=1)
            if s > 1e-10:
                rs_vals.append(r / s)

        if rs_vals:
            rs_list.append(np.mean(rs_vals))
            ns_list.append(chunk_size)

    if len(rs_list) < 3:
        return np.nan

    log_n = np.log(ns_list)
    log_rs = np.log(rs_list)

    H = np.polyfit(log_n, log_rs, 1)[0]
    return float(H)


def compute_mean_reversion(
    spread: pd.Series,
    window: int = 720,
    step: int = 24,
) -> pd.DataFrame:
    """Rolling half-life and Hurst exponent.

    Computed every `step` bars and forward-filled (expensive).

    Returns:
        DataFrame with: halflife, hurst
    """
    n = len(spread)
    halflife = pd.Series(np.nan, index=spread.index)
    hurst = pd.Series(np.nan, index=spread.index)

    for i in range(window, n, step):
        segment = spread.iloc[i - window : i]
        halflife.iloc[i] = halflife_ou(segment)
        hurst.iloc[i] = hurst_rs(segment)

    return pd.DataFrame({
        "halflife": halflife.ffill(),
        "hurst": hurst.ffill(),
    }, index=spread.index)
