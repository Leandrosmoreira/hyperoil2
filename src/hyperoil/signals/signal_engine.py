"""Signal engine — orchestrates all signal computations on rolling buffer.

Maintains candle buffers and recomputes features on each new bar.
Provides both batch (DataFrame) and single-point (real-time) APIs.
"""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np
import pandas as pd

from hyperoil.config import SignalConfig
from hyperoil.observability.logger import get_logger
from hyperoil.signals.correlation import compute_correlation
from hyperoil.signals.regime_filter import classify_regime_single, compute_regime
from hyperoil.signals.spread import compute_spread
from hyperoil.signals.volatility import compute_volatility
from hyperoil.signals.zscore import compute_zscore
from hyperoil.types import Regime, SpreadSnapshot, now_ms

log = get_logger(__name__)


class SignalEngine:
    """Computes trading signals from rolling candle buffers."""

    def __init__(self, config: SignalConfig, buffer_size: int = 500) -> None:
        self._config = config
        self._buffer_size = buffer_size

        # Candle buffers: list of dicts with {timestamp_ms, open, high, low, close, volume, mid}
        self._candles_left: deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self._candles_right: deque[dict[str, Any]] = deque(maxlen=buffer_size)

        # Cached results
        self._last_features: pd.DataFrame | None = None
        self._last_snapshot: SpreadSnapshot | None = None

        # Minimum bars needed before signals are valid
        self._min_bars = max(config.z_window, config.beta_window, 60) + 20

    @property
    def ready(self) -> bool:
        return (
            len(self._candles_left) >= self._min_bars
            and len(self._candles_right) >= self._min_bars
        )

    @property
    def bars_left(self) -> int:
        return len(self._candles_left)

    @property
    def bars_right(self) -> int:
        return len(self._candles_right)

    @property
    def latest_snapshot(self) -> SpreadSnapshot | None:
        return self._last_snapshot

    @property
    def latest_features(self) -> pd.DataFrame | None:
        return self._last_features

    def add_candle(
        self,
        symbol: str,
        timestamp_ms: int,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
        mid: float = 0.0,
    ) -> None:
        """Add a new candle to the buffer."""
        candle = {
            "timestamp_ms": timestamp_ms,
            "open": open,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "mid": mid if mid > 0 else close,
        }

        if symbol in ("CL",) or symbol.endswith(":CL"):
            self._candles_left.append(candle)
        elif symbol in ("BRENTOIL",) or symbol.endswith(":BRENTOIL"):
            self._candles_right.append(candle)
        else:
            log.warning("signal_engine_unknown_symbol", symbol=symbol)

    def load_history(
        self,
        candles_left: list[dict[str, Any]],
        candles_right: list[dict[str, Any]],
    ) -> None:
        """Pre-load historical candles into buffers."""
        for c in candles_left:
            self._candles_left.append(c)
        for c in candles_right:
            self._candles_right.append(c)
        log.info(
            "signal_engine_history_loaded",
            left=len(candles_left),
            right=len(candles_right),
        )

    def compute(self) -> SpreadSnapshot | None:
        """Recompute all signals on current buffer.

        Returns latest SpreadSnapshot or None if not enough data.
        """
        if not self.ready:
            log.debug(
                "signal_engine_not_ready",
                bars_left=self.bars_left,
                bars_right=self.bars_right,
                min_bars=self._min_bars,
            )
            return None

        cfg = self._config

        # Build aligned DataFrame
        df = self._build_dataframe()
        if df is None or len(df) < self._min_bars:
            return None

        # Compute returns
        df["ret_left"] = np.log(df["price_left"]).diff()
        df["ret_right"] = np.log(df["price_right"]).diff()

        # Spread + hedge ratio
        df = compute_spread(
            df,
            mode=cfg.spread_mode,
            hedge_mode=cfg.hedge_mode,
            hedge_window=cfg.beta_window,
        )

        # Z-score
        z_df = compute_zscore(
            df["spread"],
            window=cfg.z_window,
            min_std=cfg.min_std,
        )
        df["spread_mean"] = z_df["spread_mean"]
        df["spread_std"] = z_df["spread_std"]
        df["zscore"] = z_df["zscore"]

        # Correlation
        corr_df = compute_correlation(
            df["price_left"], df["price_right"],
            df["ret_left"], df["ret_right"],
            window=cfg.correlation_window,
        )
        df["correlation_prices"] = corr_df["correlation_prices"]
        df["correlation_returns"] = corr_df["correlation_returns"]

        # Volatility
        vol_df = compute_volatility(
            df["ret_left"], df["ret_right"], df["spread"],
            window=cfg.volatility_window,
        )
        df["vol_left"] = vol_df["vol_left"]
        df["vol_right"] = vol_df["vol_right"]
        df["vol_spread"] = vol_df["vol_spread"]
        df["vol_regime"] = vol_df["vol_regime"]

        # Regime
        regime_df = compute_regime(
            df["correlation_returns"],
            df["vol_regime"],
            df["spread"],
        )
        df["spread_slope"] = regime_df["spread_slope"]
        df["regime"] = regime_df["regime"]
        df["regime_valid"] = regime_df["regime_valid"]

        # Drop rows without valid signals
        df = df.dropna(subset=["zscore", "hedge_ratio"]).reset_index(drop=True)

        if len(df) == 0:
            return None

        self._last_features = df

        # Build latest snapshot
        row = df.iloc[-1]
        regime_val = row.get("regime", Regime.UNKNOWN.value)
        try:
            regime = Regime(regime_val)
        except ValueError:
            regime = Regime.UNKNOWN

        self._last_snapshot = SpreadSnapshot(
            timestamp_ms=int(row.get("timestamp_ms", now_ms())),
            price_left=float(row["price_left"]),
            price_right=float(row["price_right"]),
            beta=float(row["hedge_ratio"]),
            spread=float(row["spread"]),
            spread_mean=float(row["spread_mean"]),
            spread_std=float(row["spread_std"]),
            zscore=float(row["zscore"]),
            correlation=float(row.get("correlation_returns", 0.0)),
            vol_left=float(row.get("vol_left", 0.0)),
            vol_right=float(row.get("vol_right", 0.0)),
            regime=regime,
        )

        log.debug(
            "signal_computed",
            zscore=round(self._last_snapshot.zscore, 4),
            beta=round(self._last_snapshot.beta, 4),
            regime=regime.value,
            correlation=round(self._last_snapshot.correlation, 4),
        )

        return self._last_snapshot

    def _build_dataframe(self) -> pd.DataFrame | None:
        """Build aligned DataFrame from candle buffers."""
        df_left = pd.DataFrame(list(self._candles_left))
        df_right = pd.DataFrame(list(self._candles_right))

        if df_left.empty or df_right.empty:
            return None

        # Use close price as primary price source
        price_col = "mid" if self._config.price_source == "mid" else "close"

        df_left = df_left.rename(columns={
            "timestamp_ms": "timestamp_ms",
            price_col: "price_left",
        })[["timestamp_ms", "price_left"]].copy()

        df_right = df_right.rename(columns={
            price_col: "price_right",
        })

        if "timestamp_ms" in df_right.columns:
            df_right = df_right[["timestamp_ms", "price_right"]].copy()
        else:
            df_right = df_right[["price_right"]].copy()

        # Align by position (same index) — assumes synced candle feeds
        min_len = min(len(df_left), len(df_right))
        df_left = df_left.tail(min_len).reset_index(drop=True)
        df_right = df_right.tail(min_len).reset_index(drop=True)

        df = pd.DataFrame({
            "timestamp_ms": df_left["timestamp_ms"],
            "price_left": df_left["price_left"].astype(float),
            "price_right": df_right["price_right"].astype(float),
        })

        # Remove zero/negative prices
        df = df[(df["price_left"] > 0) & (df["price_right"] > 0)]

        return df if len(df) > 0 else None

    # --- Convenience properties ---

    @property
    def current_z(self) -> float | None:
        return self._last_snapshot.zscore if self._last_snapshot else None

    @property
    def current_regime(self) -> Regime:
        return self._last_snapshot.regime if self._last_snapshot else Regime.UNKNOWN

    @property
    def current_beta(self) -> float | None:
        return self._last_snapshot.beta if self._last_snapshot else None

    @property
    def current_correlation(self) -> float | None:
        return self._last_snapshot.correlation if self._last_snapshot else None
