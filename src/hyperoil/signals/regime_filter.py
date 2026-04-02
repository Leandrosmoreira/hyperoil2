"""Regime classification — rules-based GOOD/CAUTION/BAD.

Ported from v1 — validated math preserved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from hyperoil.types import Regime


def _compute_trend_slope(
    spread: pd.Series,
    window: int = 48,
) -> pd.Series:
    """Rolling linear regression slope of spread — detects trending."""
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = np.sum((x - x_mean) ** 2)

    def _slope(chunk: np.ndarray) -> float:
        if len(chunk) < window or np.any(np.isnan(chunk)):
            return np.nan
        y = chunk
        return float(np.sum((x - x_mean) * (y - y.mean())) / x_var)

    return spread.rolling(window).apply(_slope, raw=True)


def compute_regime(
    correlation_returns: pd.Series,
    vol_regime: pd.Series,
    spread: pd.Series,
    min_correlation: float = 0.70,
    max_trend_slope: float = 0.01,
) -> pd.DataFrame:
    """Classify each bar into GOOD/CAUTION/BAD regime.

    Rules:
        GOOD:    low/normal vol AND corr >= min_corr AND no trend
        CAUTION: (not BAD and not GOOD)
        BAD:     extreme vol OR corr < 0.50 OR strong trend

    Returns:
        DataFrame with: spread_slope, regime, regime_valid
    """
    vol_blocked = {"extreme"}
    spread_slope = _compute_trend_slope(spread)

    conditions_bad = (
        (vol_regime.isin(vol_blocked))
        | (correlation_returns < 0.50)
        | (spread_slope.abs() > max_trend_slope * 2)
    )

    conditions_good = (
        (~vol_regime.isin(vol_blocked))
        & (correlation_returns >= min_correlation)
        & (spread_slope.abs() <= max_trend_slope)
    )

    regime = pd.Series(
        np.where(
            conditions_bad, Regime.BAD.value,
            np.where(conditions_good, Regime.GOOD.value, Regime.CAUTION.value),
        ),
        index=spread.index,
    )

    return pd.DataFrame({
        "spread_slope": spread_slope,
        "regime": regime,
        "regime_valid": regime == Regime.GOOD.value,
    }, index=spread.index)


def classify_regime_single(
    correlation: float,
    vol_regime: str,
    spread_slope: float,
    min_correlation: float = 0.70,
    max_trend_slope: float = 0.01,
) -> Regime:
    """Classify a single point-in-time regime. For real-time use."""
    if vol_regime == "extreme" or correlation < 0.50 or abs(spread_slope) > max_trend_slope * 2:
        return Regime.BAD
    if correlation >= min_correlation and abs(spread_slope) <= max_trend_slope:
        return Regime.GOOD
    return Regime.CAUTION
