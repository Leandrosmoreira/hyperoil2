"""Tests for the backtest simulator."""

from __future__ import annotations

import numpy as np
import pandas as pd

from hyperoil.backtest.replay_engine import ReplayEngine
from hyperoil.backtest.simulator import Simulator
from hyperoil.config import AppConfig, GridLevelConfig


def _generate_mean_reverting_pair(
    n: int = 600,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate synthetic mean-reverting pair data.

    CL and BRENT are cointegrated with beta ≈ 0.95.
    The spread mean-reverts, creating trading opportunities.
    """
    rng = np.random.default_rng(seed)

    # Common factor (oil price trend)
    common = 70.0 + np.cumsum(rng.normal(0, 0.05, n))

    # CL tracks common with some noise
    cl_noise = np.cumsum(rng.normal(0, 0.02, n))
    cl = common * 0.97 + cl_noise

    # BRENT tracks common with beta ≈ 0.95 and mean-reverting spread
    spread = np.zeros(n)
    spread[0] = rng.normal(0, 0.01)
    for i in range(1, n):
        # OU process: mean-reverting spread
        spread[i] = spread[i - 1] * 0.95 + rng.normal(0, 0.015)

    brent = common * 1.03 + spread

    timestamps = list(range(1000, 1000 + n * 900_000, 900_000))

    df_left = pd.DataFrame({
        "timestamp_ms": timestamps,
        "open": cl,
        "high": cl + rng.uniform(0, 0.3, n),
        "low": cl - rng.uniform(0, 0.3, n),
        "close": cl + rng.normal(0, 0.02, n),
        "volume": rng.uniform(100, 500, n),
    })

    df_right = pd.DataFrame({
        "timestamp_ms": timestamps,
        "open": brent,
        "high": brent + rng.uniform(0, 0.3, n),
        "low": brent - rng.uniform(0, 0.3, n),
        "close": brent + rng.normal(0, 0.02, n),
        "volume": rng.uniform(100, 500, n),
    })

    return df_left, df_right


def _test_config() -> AppConfig:
    """Config tuned for synthetic data testing."""
    return AppConfig.model_validate({
        "symbols": {"left": "CL", "right": "BRENTOIL"},
        "signal": {
            "z_window": 100,
            "beta_window": 80,
            "correlation_window": 80,
            "volatility_window": 80,
            "hedge_mode": "rolling_ols",
        },
        "grid": {
            "entry_z": 1.5,
            "exit_z": 0.2,
            "stop_z": 4.5,
            "cooldown_bars": 2,
            "max_levels": 3,
            "levels": [
                {"z": 1.5, "mult": 1.0},
                {"z": 2.0, "mult": 1.2},
                {"z": 2.5, "mult": 1.5},
            ],
        },
        "sizing": {
            "base_notional_usd": 100.0,
            "max_notional_per_cycle": 1000.0,
        },
        "risk": {
            "max_daily_loss_usd": 500.0,
            "max_cycle_loss_usd": 200.0,
            "max_consecutive_losses": 10,
            "cooldown_after_stop_bars": 3,
            "min_correlation": 0.3,
            "max_cycle_minutes": 99999,
        },
        "backtest": {
            "fee_taker_bps": 3.5,
            "slippage_fixed_bps": 1.0,
            "slippage_proportional_bps": 0.5,
        },
    })


class TestSimulator:
    def test_runs_without_error(self) -> None:
        df_left, df_right = _generate_mean_reverting_pair(600)
        replay = ReplayEngine(df_left, df_right)
        sim = Simulator(_test_config())
        result = sim.run(replay)

        assert result.total_bars > 0
        assert result.signals_generated > 0

    def test_produces_equity_curve(self) -> None:
        df_left, df_right = _generate_mean_reverting_pair(600)
        replay = ReplayEngine(df_left, df_right)
        sim = Simulator(_test_config())
        result = sim.run(replay)

        assert len(result.equity_curve) > 0

    def test_deterministic(self) -> None:
        """Same data + same config = same result."""
        df_left, df_right = _generate_mean_reverting_pair(600)

        replay1 = ReplayEngine(df_left.copy(), df_right.copy())
        sim1 = Simulator(_test_config())
        result1 = sim1.run(replay1)

        replay2 = ReplayEngine(df_left.copy(), df_right.copy())
        sim2 = Simulator(_test_config())
        result2 = sim2.run(replay2)

        assert len(result1.trades) == len(result2.trades)
        assert len(result1.equity_curve) == len(result2.equity_curve)
        if result1.trades and result2.trades:
            assert result1.trades[0].net_pnl == result2.trades[0].net_pnl

    def test_trades_have_fees(self) -> None:
        df_left, df_right = _generate_mean_reverting_pair(600)
        replay = ReplayEngine(df_left, df_right)
        sim = Simulator(_test_config())
        result = sim.run(replay)

        if result.trades:
            # At least some trades should have non-zero fees
            total_fees = sum(t.fees for t in result.trades)
            assert total_fees > 0

    def test_no_open_cycle_at_end(self) -> None:
        """Simulator should force-close any open cycle at the end."""
        df_left, df_right = _generate_mean_reverting_pair(600)
        replay = ReplayEngine(df_left, df_right)
        sim = Simulator(_test_config())
        sim.run(replay)

        assert not sim._cycle_mgr.has_open_cycle

    def test_short_data_no_crash(self) -> None:
        """With very short data, should run without error."""
        df_left, df_right = _generate_mean_reverting_pair(50)
        replay = ReplayEngine(df_left, df_right)
        sim = Simulator(_test_config())
        result = sim.run(replay)
        # May have 0 trades (not enough data to warm up), but shouldn't crash
        assert result.total_bars >= 0
