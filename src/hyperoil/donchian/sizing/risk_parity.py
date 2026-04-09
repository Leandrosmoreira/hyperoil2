"""Inverse-volatility (risk parity) weighting.

Each asset gets a weight inversely proportional to its realized volatility:

    w[i] = (1 / vol[i]) / Σ_j (1 / vol[j])

so that low-vol assets get bigger allocations and high-vol assets get smaller
ones — equalizing each asset's risk contribution to the portfolio.

Degenerate inputs (zero, negative, or non-finite volatility) are filtered out
of the weighting; the remaining assets are re-normalized to sum to 1.0. A
symbol with vol == 0 (a dead/stale series) would otherwise produce w = ∞ and
poison the entire weight vector.
"""

from __future__ import annotations

import math

import numpy as np

from hyperoil.observability.logger import get_logger

log = get_logger(__name__)

# 4h candles per year = 6 per day × 365 days. Used to annualize std of 4h
# log-returns into the conventional annualized volatility figure.
PERIODS_PER_YEAR_4H = 6 * 365  # 2190


def compute_realized_vol(
    closes: np.ndarray,
    window: int,
    periods_per_year: int = PERIODS_PER_YEAR_4H,
) -> float:
    """Annualized realized volatility from log returns of the last `window` bars.

    Uses a rolling window of (window + 1) closes to produce `window` log
    returns, std with ddof=1, then annualized by sqrt(periods_per_year).
    Returns NaN if there is not enough data — caller decides what to do.
    """
    if window <= 1:
        raise ValueError(f"window must be > 1, got {window}")
    if len(closes) < window + 1:
        return float("nan")

    sl = np.asarray(closes[-window - 1 :], dtype=float)
    if (sl <= 0).any():
        # log of non-positive is undefined — bail out
        return float("nan")

    log_rets = np.diff(np.log(sl))
    sigma = float(np.std(log_rets, ddof=1))
    return sigma * math.sqrt(periods_per_year)


class RiskParityEngine:
    """Stateless inverse-volatility weighting engine."""

    def compute_weights(self, vols: dict[str, float]) -> dict[str, float]:
        """Return per-symbol weights summing to 1.0.

        Symbols with non-finite or non-positive vol are dropped from the
        output (NOT given weight 0 in the output dict — they are absent).
        Empty / all-degenerate inputs return an empty dict.
        """
        valid: dict[str, float] = {}
        for sym, v in vols.items():
            if v is None or not math.isfinite(v) or v <= 0.0:
                log.warning("risk_parity_skipped_symbol", symbol=sym, vol=v)
                continue
            valid[sym] = v

        if not valid:
            return {}

        inv = {sym: 1.0 / v for sym, v in valid.items()}
        denom = sum(inv.values())
        if denom <= 0.0 or not math.isfinite(denom):
            return {}

        return {sym: x / denom for sym, x in inv.items()}
