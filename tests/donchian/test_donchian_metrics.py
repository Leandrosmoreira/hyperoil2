"""Tests for the Donchian metrics module — equity-curve based stats."""

from __future__ import annotations

import math

import pytest

from hyperoil.donchian.backtest.metrics import (
    compute_donchian_metrics,
    format_donchian_report,
)
from hyperoil.donchian.backtest.simulator import SimulationResult, TradeRecord


HOUR_MS = 3600 * 1000


def _result(equity: list[float], timestamps: list[int] | None = None,
            trades: list[TradeRecord] | None = None) -> SimulationResult:
    if timestamps is None:
        timestamps = [HOUR_MS * 4 * i for i in range(len(equity))]
    return SimulationResult(
        initial_capital=equity[0],
        final_equity=equity[-1],
        n_bars=len(equity),
        n_warmup_bars=0,
        n_trades=len(trades or []),
        equity_curve=list(equity),
        timestamps=timestamps,
        trades=trades or [],
    )


def test_flat_curve_zero_metrics():
    r = _result([10_000.0] * 50)
    m = compute_donchian_metrics(r)
    assert m.total_return_pct == 0.0
    assert m.cagr == pytest.approx(0.0, abs=1e-9)
    assert m.sharpe == 0.0
    assert m.max_drawdown_pct == 0.0


def test_monotonic_uptrend_positive_sharpe():
    eq = [10_000 * (1.001 ** i) for i in range(100)]
    m = compute_donchian_metrics(_result(eq))
    assert m.total_return_pct > 0
    assert m.sharpe > 0
    assert m.max_drawdown_pct == 0.0
    assert math.isinf(m.calmar)  # No drawdown → calmar = inf


def test_drawdown_recovery_metric():
    # 10000 → 12000 → 9000 → 11000
    eq = [10000, 11000, 12000, 11000, 10000, 9000, 9500, 10500, 11000]
    m = compute_donchian_metrics(_result(eq))
    # Peak was 12000, trough 9000 → DD = 25%
    assert m.max_drawdown_pct == pytest.approx(0.25, rel=1e-9)


def test_realized_pnl_breakdown():
    eq = [10_000.0, 10_500.0, 11_000.0]
    trades = [
        TradeRecord(timestamp_ms=1, symbol="BTC", action="exit", price=100,
                    size_usd_before=1000, size_usd_after=0,
                    realized_pnl=300.0, fee=2.0, score=0.6, leverage=1.5, reason="profit"),
        TradeRecord(timestamp_ms=2, symbol="ETH", action="exit", price=50,
                    size_usd_before=500, size_usd_after=0,
                    realized_pnl=-100.0, fee=1.0, score=0.5, leverage=1.0, reason="stop"),
    ]
    m = compute_donchian_metrics(_result(eq, trades=trades))
    assert m.n_trades == 2
    assert m.n_winning_trades == 1
    assert m.n_losing_trades == 1
    assert m.win_rate == 0.5
    assert m.profit_factor == pytest.approx(3.0)
    assert m.total_realized_pnl == pytest.approx(200.0)
    assert m.total_fees == pytest.approx(3.0)


def test_format_report_runs():
    eq = [10_000.0, 10_500.0, 11_000.0]
    m = compute_donchian_metrics(_result(eq))
    text = format_donchian_report(m)
    assert "DONCHIAN ENSEMBLE" in text
    assert "Sharpe" in text
