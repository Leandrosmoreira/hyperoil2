"""Rolling correlation computation.

Ported from v1 — validated math preserved.
"""

from __future__ import annotations

import pandas as pd


def compute_correlation(
    price_left: pd.Series,
    price_right: pd.Series,
    ret_left: pd.Series,
    ret_right: pd.Series,
    window: int = 168,
) -> pd.DataFrame:
    """Compute rolling price and return correlations.

    Args:
        price_left, price_right: price series
        ret_left, ret_right: log return series
        window: rolling window

    Returns:
        DataFrame with: correlation_prices, correlation_returns
    """
    return pd.DataFrame({
        "correlation_prices": price_left.rolling(window).corr(price_right),
        "correlation_returns": ret_left.rolling(window).corr(ret_right),
    }, index=price_left.index)
