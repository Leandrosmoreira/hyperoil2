"""Spread computation with multiple hedge ratio modes.

Ported from v1 — validated math preserved.
v2 additions: Kalman filter mode, vol-adjusted mode, incremental API.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_hedge_ratio_fixed(
    price_left: pd.Series,
    price_right: pd.Series,
) -> pd.Series:
    """Full-sample OLS hedge ratio: log(left) = alpha + beta * log(right)."""
    y = np.log(price_left)
    x = np.log(price_right)

    valid = np.isfinite(y) & np.isfinite(x)
    if valid.sum() < 10:
        return pd.Series(np.nan, index=price_left.index, name="hedge_ratio")

    x_v, y_v = x[valid], y[valid]
    beta = np.cov(x_v, y_v)[0, 1] / np.var(x_v)

    return pd.Series(beta, index=price_left.index, name="hedge_ratio")


def compute_hedge_ratio_rolling(
    price_left: pd.Series,
    price_right: pd.Series,
    window: int = 168,
) -> pd.Series:
    """Rolling OLS hedge ratio on log prices."""
    y = np.log(price_left)
    x = np.log(price_right)

    cov_xy = y.rolling(window).cov(x)
    var_x = x.rolling(window).var()

    beta = cov_xy / var_x
    beta.name = "hedge_ratio"
    return beta


def compute_hedge_ratio_vol_adjusted(
    ret_left: pd.Series,
    ret_right: pd.Series,
    window: int = 168,
) -> pd.Series:
    """Volatility-adjusted hedge ratio: vol_left / vol_right."""
    vol_left = ret_left.rolling(window).std()
    vol_right = ret_right.rolling(window).std()

    beta = vol_left / vol_right.clip(lower=1e-10)
    beta.name = "hedge_ratio"
    return beta


def compute_hedge_ratio_kalman(
    price_left: pd.Series,
    price_right: pd.Series,
    delta: float = 1e-4,
    ve: float = 1e-3,
) -> pd.Series:
    """Kalman filter hedge ratio — adapts faster than rolling OLS.

    Uses a simple 1-state Kalman filter where:
      - state = beta (hedge ratio)
      - observation: log(left) = beta * log(right) + noise
    """
    y = np.log(price_left.values)
    x = np.log(price_right.values)
    n = len(y)

    beta = np.full(n, np.nan)
    R = 1.0       # state covariance
    beta_hat = 1.0  # initial estimate

    for i in range(n):
        if not (np.isfinite(y[i]) and np.isfinite(x[i])):
            if i > 0:
                beta[i] = beta[i - 1]
            continue

        # Predict
        R = R + delta

        # Update
        H = x[i]
        y_hat = H * beta_hat
        e = y[i] - y_hat
        S = H * R * H + ve
        K = R * H / S

        beta_hat = beta_hat + K * e
        R = (1 - K * H) * R

        beta[i] = beta_hat

    return pd.Series(beta, index=price_left.index, name="hedge_ratio")


def compute_spread(
    df: pd.DataFrame,
    mode: str = "log",
    hedge_mode: str = "rolling_ols",
    hedge_window: int = 168,
    kalman_delta: float = 1e-4,
    kalman_ve: float = 1e-3,
) -> pd.DataFrame:
    """Compute hedge ratio and spread.

    Args:
        df: DataFrame with columns: price_left, price_right
            Optionally: ret_left, ret_right (for vol_adjusted mode)
        mode: "log" or "linear"
        hedge_mode: "fixed" | "rolling_ols" | "vol_adjusted" | "kalman"
        hedge_window: window for rolling computations
        kalman_delta: Kalman filter transition covariance
        kalman_ve: Kalman filter observation noise

    Returns:
        df with added columns: hedge_ratio, spread
    """
    pl = df["price_left"]
    pr = df["price_right"]

    if hedge_mode == "fixed":
        df["hedge_ratio"] = compute_hedge_ratio_fixed(pl, pr)
    elif hedge_mode == "vol_adjusted":
        if "ret_left" not in df.columns:
            df["ret_left"] = np.log(pl).diff()
        if "ret_right" not in df.columns:
            df["ret_right"] = np.log(pr).diff()
        df["hedge_ratio"] = compute_hedge_ratio_vol_adjusted(
            df["ret_left"], df["ret_right"], window=hedge_window,
        )
    elif hedge_mode == "kalman":
        df["hedge_ratio"] = compute_hedge_ratio_kalman(
            pl, pr, delta=kalman_delta, ve=kalman_ve,
        )
    else:  # rolling_ols (default)
        df["hedge_ratio"] = compute_hedge_ratio_rolling(pl, pr, window=hedge_window)

    beta = df["hedge_ratio"]

    if mode == "log":
        df["spread"] = np.log(pl) - beta * np.log(pr)
    else:
        df["spread"] = pl - beta * pr

    return df
