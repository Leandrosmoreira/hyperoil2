"""Z-score computation with rolling statistics.

Ported from v1 — validated math preserved.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_zscore(
    spread: pd.Series,
    window: int = 300,
    min_std: float = 0.0001,
) -> pd.DataFrame:
    """Compute rolling z-score of a spread series.

    Args:
        spread: spread values
        window: rolling window for mean/std
        min_std: minimum std to avoid division by near-zero

    Returns:
        DataFrame with columns: spread_mean, spread_std, zscore
    """
    spread_mean = spread.rolling(window).mean()
    spread_std = spread.rolling(window).std()

    # Guard against near-zero std
    safe_std = spread_std.clip(lower=min_std)

    zscore = (spread - spread_mean) / safe_std

    return pd.DataFrame({
        "spread_mean": spread_mean,
        "spread_std": spread_std,
        "zscore": zscore,
    }, index=spread.index)


def zscore_single(
    current_spread: float,
    mean: float,
    std: float,
    min_std: float = 0.0001,
) -> float:
    """Compute z-score for a single value. For real-time incremental use."""
    safe_std = max(std, min_std)
    return (current_spread - mean) / safe_std
