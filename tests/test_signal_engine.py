"""Tests for signal engine orchestrator."""

from __future__ import annotations

import numpy as np
import pytest

from hyperoil.config import SignalConfig
from hyperoil.signals.signal_engine import SignalEngine
from hyperoil.types import Regime


def _generate_correlated_candles(
    n: int = 300,
    base_left: float = 68.0,
    base_right: float = 72.0,
    beta: float = 0.95,
    noise: float = 0.001,
    seed: int = 42,
) -> tuple[list[dict], list[dict]]:
    """Generate synthetic correlated candle pairs (CL/BRENT)."""
    rng = np.random.RandomState(seed)
    candles_left = []
    candles_right = []
    price_right = base_right
    price_left = base_left
    ts = 1700000000000

    for i in range(n):
        # Shared factor drives correlation
        common = rng.normal(0, 0.003)
        ret_right = common + rng.normal(0, noise)
        ret_left = beta * common + rng.normal(0, noise)

        price_right = price_right * np.exp(ret_right)
        price_left = price_left * np.exp(ret_left)

        for price, candles in [(price_left, candles_left), (price_right, candles_right)]:
            candles.append({
                "timestamp_ms": ts + i * 900000,
                "open": price * (1 + rng.normal(0, 0.0005)),
                "high": price * (1 + abs(rng.normal(0, 0.001))),
                "low": price * (1 - abs(rng.normal(0, 0.001))),
                "close": price,
                "volume": abs(rng.normal(1000, 200)),
                "mid": price,
            })

    return candles_left, candles_right


class TestSignalEngine:
    def test_not_ready_without_data(self) -> None:
        engine = SignalEngine(SignalConfig())
        assert not engine.ready
        assert engine.compute() is None

    def test_not_ready_with_insufficient_data(self) -> None:
        engine = SignalEngine(SignalConfig(z_window=100, beta_window=50), buffer_size=500)
        cl, brent = _generate_correlated_candles(50)
        engine.load_history(cl, brent)
        assert not engine.ready

    def test_ready_with_sufficient_data(self) -> None:
        config = SignalConfig(z_window=100, beta_window=50)
        engine = SignalEngine(config, buffer_size=500)

        cl, brent = _generate_correlated_candles(300)
        engine.load_history(cl, brent)

        assert engine.ready
        assert engine.bars_left == 300
        assert engine.bars_right == 300

    def test_compute_returns_snapshot(self) -> None:
        config = SignalConfig(z_window=100, beta_window=50)
        engine = SignalEngine(config, buffer_size=500)

        cl, brent = _generate_correlated_candles(300)
        engine.load_history(cl, brent)

        snapshot = engine.compute()
        assert snapshot is not None
        assert snapshot.price_left > 0
        assert snapshot.price_right > 0
        assert snapshot.beta > 0  # correlated pair should have positive beta
        assert np.isfinite(snapshot.zscore)
        assert snapshot.regime in (Regime.GOOD, Regime.CAUTION, Regime.BAD)

    def test_convenience_properties(self) -> None:
        config = SignalConfig(z_window=100, beta_window=50)
        engine = SignalEngine(config, buffer_size=500)

        cl, brent = _generate_correlated_candles(300)
        engine.load_history(cl, brent)
        engine.compute()

        assert engine.current_z is not None
        assert engine.current_beta is not None
        assert engine.current_correlation is not None
        assert engine.current_regime in (Regime.GOOD, Regime.CAUTION, Regime.BAD)

    def test_add_candle_increments_buffer(self) -> None:
        engine = SignalEngine(SignalConfig())

        engine.add_candle(
            symbol="CL",
            timestamp_ms=1700000000000,
            open=68.0, high=68.5, low=67.5, close=68.2, volume=100, mid=68.1,
        )
        assert engine.bars_left == 1

        engine.add_candle(
            symbol="BRENTOIL",
            timestamp_ms=1700000000000,
            open=72.0, high=72.5, low=71.5, close=72.2, volume=80, mid=72.1,
        )
        assert engine.bars_right == 1

    def test_add_candle_with_prefix(self) -> None:
        engine = SignalEngine(SignalConfig())
        engine.add_candle(
            symbol="xyz:CL",
            timestamp_ms=1700000000000,
            open=68.0, high=68.5, low=67.5, close=68.2, volume=100,
        )
        assert engine.bars_left == 1

    def test_features_dataframe_available(self) -> None:
        config = SignalConfig(z_window=100, beta_window=50)
        engine = SignalEngine(config, buffer_size=500)

        cl, brent = _generate_correlated_candles(300)
        engine.load_history(cl, brent)
        engine.compute()

        df = engine.latest_features
        assert df is not None
        assert "zscore" in df.columns
        assert "hedge_ratio" in df.columns
        assert "spread" in df.columns
        assert "regime" in df.columns
        assert "correlation_returns" in df.columns
        assert "vol_left" in df.columns
