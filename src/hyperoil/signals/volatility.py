"""Volatility computation and regime classification.

Ported from v1 — validated math preserved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_realized_vol(returns: pd.Series, window: int = 24) -> pd.Series:
    """Annualized realized volatility from log returns."""
    return returns.rolling(window).std() * np.sqrt(365 * 24 / window)


def compute_volatility(
    ret_left: pd.Series,
    ret_right: pd.Series,
    spread: pd.Series,
    window: int = 24,
    regime_thresholds: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Compute volatility metrics and regime.

    Args:
        ret_left, ret_right: log return series
        spread: spread series (for spread volatility)
        window: rolling window
        regime_thresholds: quantile thresholds for regime classification

    Returns:
        DataFrame with: vol_left, vol_right, vol_spread, vol_regime
    """
    if regime_thresholds is None:
        regime_thresholds = {"low": 0.25, "normal": 0.50, "high": 0.75}

    vol_left = compute_realized_vol(ret_left, window)
    vol_right = compute_realized_vol(ret_right, window)

    spread_ret = spread.diff()
    vol_spread = spread_ret.rolling(window).std() * np.sqrt(365 * 24 / window)

    # Classify regime based on vol_spread quantiles
    vol_clean = vol_spread.dropna()
    if len(vol_clean) > 0:
        q_low = vol_clean.quantile(regime_thresholds["low"])
        q_normal = vol_clean.quantile(regime_thresholds["normal"])
        q_high = vol_clean.quantile(regime_thresholds["high"])

        conditions = [
            vol_spread <= q_low,
            vol_spread <= q_normal,
            vol_spread <= q_high,
        ]
        choices = ["low", "normal", "high"]
        vol_regime = pd.Series(
            np.select(conditions, choices, default="extreme"),
            index=spread.index,
        )
    else:
        vol_regime = pd.Series(np.nan, index=spread.index)

    return pd.DataFrame({
        "vol_left": vol_left,
        "vol_right": vol_right,
        "vol_spread": vol_spread,
        "vol_regime": vol_regime,
    }, index=spread.index)
