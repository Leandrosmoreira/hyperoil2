"""Rolling cointegration tests (ADF on spread).

Ported from v1 — validated math preserved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller


def _adf_pvalue(series: pd.Series) -> float:
    """Run ADF test, return p-value. Returns NaN on failure."""
    try:
        clean = series.dropna()
        if len(clean) < 20:
            return np.nan
        result = adfuller(clean, maxlag=1, regression="c", autolag=None)
        return float(result[1])
    except Exception:
        return np.nan


def compute_cointegration(
    spread: pd.Series,
    window: int = 720,
    step: int = 24,
    p_threshold: float = 0.05,
) -> pd.DataFrame:
    """Rolling ADF p-value and cointegration flag.

    ADF is expensive — computed every `step` bars, forward-filled.

    Args:
        spread: spread series
        window: rolling window for ADF test
        step: compute every N bars
        p_threshold: p-value threshold for cointegration

    Returns:
        DataFrame with: adf_pvalue, is_cointegrated
    """
    n = len(spread)
    pvalues = pd.Series(np.nan, index=spread.index)

    for i in range(window, n, step):
        segment = spread.iloc[i - window : i]
        pvalues.iloc[i] = _adf_pvalue(segment)

    pvalues = pvalues.ffill()

    return pd.DataFrame({
        "adf_pvalue": pvalues,
        "is_cointegrated": pvalues < p_threshold,
    }, index=spread.index)
