"""Tests for performance metrics."""

from __future__ import annotations

from hyperoil.backtest.metrics import (
    PerformanceMetrics,
    compute_metrics,
    format_report,
    _compute_drawdown,
    _compute_sharpe,
    _compute_sortino,
)
from hyperoil.backtest.simulator import SimulationResult, TradeRecord


def _make_trade(net_pnl: float, bars: int = 10, levels: int = 1, reason: str = "take_profit", direction: str = "long_spread") -> TradeRecord:
    return TradeRecord(
        cycle_id="c1",
        direction=direction,
        levels_used=levels,
        entry_z_avg=-1.6,
        exit_z=-0.1,
        bars_held=bars,
        gross_pnl=net_pnl + 0.5,
        fees=0.5,
        net_pnl=net_pnl,
        entry_timestamp_ms=1000,
        exit_timestamp_ms=2000,
        stop_reason=reason,
    )


class TestComputeMetrics:
    def test_empty_trades(self) -> None:
        result = SimulationResult()
        metrics = compute_metrics(result)
        assert metrics.total_trades == 0
        assert metrics.total_net_pnl == 0.0

    def test_single_winning_trade(self) -> None:
        result = SimulationResult(
            trades=[_make_trade(10.0)],
            equity_curve=[10.0],
        )
        metrics = compute_metrics(result)
        assert metrics.total_trades == 1
        assert metrics.winning_trades == 1
        assert metrics.losing_trades == 0
        assert metrics.win_rate == 1.0
        assert metrics.total_net_pnl == 10.0

    def test_mixed_trades(self) -> None:
        result = SimulationResult(
            trades=[
                _make_trade(20.0),
                _make_trade(-5.0, reason="stop_loss_z"),
                _make_trade(15.0),
                _make_trade(-10.0, reason="stop_loss_monetary"),
            ],
            equity_curve=[20.0, 15.0, 30.0, 20.0],
        )
        metrics = compute_metrics(result)
        assert metrics.total_trades == 4
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 2
        assert metrics.win_rate == 0.5
        assert metrics.total_net_pnl == 20.0

    def test_profit_factor(self) -> None:
        result = SimulationResult(
            trades=[_make_trade(30.0), _make_trade(-10.0)],
            equity_curve=[30.0, 20.0],
        )
        metrics = compute_metrics(result)
        assert metrics.profit_factor == 3.0

    def test_all_losers_profit_factor(self) -> None:
        result = SimulationResult(
            trades=[_make_trade(-10.0), _make_trade(-5.0)],
            equity_curve=[-10.0, -15.0],
        )
        metrics = compute_metrics(result)
        assert metrics.profit_factor == 0.0  # no winners

    def test_trades_by_stop_reason(self) -> None:
        result = SimulationResult(
            trades=[
                _make_trade(10.0, reason="take_profit"),
                _make_trade(-5.0, reason="stop_loss_z"),
                _make_trade(8.0, reason="take_profit"),
            ],
            equity_curve=[10.0, 5.0, 13.0],
        )
        metrics = compute_metrics(result)
        assert metrics.trades_by_stop_reason["take_profit"] == 2
        assert metrics.trades_by_stop_reason["stop_loss_z"] == 1

    def test_trades_by_direction(self) -> None:
        result = SimulationResult(
            trades=[
                _make_trade(10.0, direction="long_spread"),
                _make_trade(5.0, direction="short_spread"),
                _make_trade(-3.0, direction="long_spread"),
            ],
            equity_curve=[10.0, 15.0, 12.0],
        )
        metrics = compute_metrics(result)
        assert metrics.trades_by_direction["long_spread"] == 2
        assert metrics.trades_by_direction["short_spread"] == 1

    def test_bars_and_levels(self) -> None:
        result = SimulationResult(
            trades=[
                _make_trade(10.0, bars=20, levels=1),
                _make_trade(5.0, bars=30, levels=2),
                _make_trade(8.0, bars=10, levels=3),
            ],
            equity_curve=[10.0, 15.0, 23.0],
        )
        metrics = compute_metrics(result)
        assert metrics.avg_bars_held == 20.0
        assert metrics.max_bars_held == 30
        assert metrics.avg_levels_used == 2.0
        assert metrics.max_levels_used == 3


class TestDrawdown:
    def test_no_drawdown(self) -> None:
        equity = [1.0, 2.0, 3.0, 4.0, 5.0]
        dd_usd, dd_pct, dd_dur = _compute_drawdown(equity)
        assert dd_usd == 0.0
        assert dd_dur == 0

    def test_simple_drawdown(self) -> None:
        equity = [10.0, 8.0, 6.0, 9.0, 11.0]
        dd_usd, dd_pct, dd_dur = _compute_drawdown(equity)
        assert dd_usd == 4.0  # 10 → 6
        assert abs(dd_pct - 0.4) < 0.01
        assert dd_dur == 3  # bars 1,2,3 (9.0 still below peak 10.0)

    def test_empty_equity(self) -> None:
        dd_usd, dd_pct, dd_dur = _compute_drawdown([])
        assert dd_usd == 0.0

    def test_drawdown_duration(self) -> None:
        equity = [10.0, 9.0, 8.0, 7.0, 8.0, 11.0]
        _, _, dd_dur = _compute_drawdown(equity)
        assert dd_dur == 4  # bars 1,2,3,4 (below peak until bar 5)


class TestSharpe:
    def test_zero_returns(self) -> None:
        assert _compute_sharpe([0.0, 0.0, 0.0]) == 0.0

    def test_positive_returns(self) -> None:
        returns = [1.0, 2.0, 1.5, 1.0, 2.5]
        sharpe = _compute_sharpe(returns)
        assert sharpe > 0

    def test_single_return(self) -> None:
        assert _compute_sharpe([5.0]) == 0.0


class TestSortino:
    def test_no_downside(self) -> None:
        returns = [1.0, 2.0, 3.0]
        sortino = _compute_sortino(returns)
        assert sortino == float("inf")

    def test_with_downside(self) -> None:
        returns = [1.0, -1.0, 2.0, -0.5, 1.5]
        sortino = _compute_sortino(returns)
        assert sortino > 0

    def test_single_return(self) -> None:
        assert _compute_sortino([5.0]) == 0.0


class TestFormatReport:
    def test_format_produces_string(self) -> None:
        result = SimulationResult(
            trades=[_make_trade(10.0), _make_trade(-3.0)],
            equity_curve=[10.0, 7.0],
        )
        metrics = compute_metrics(result)
        report = format_report(metrics)
        assert "BACKTEST PERFORMANCE REPORT" in report
        assert "Win Rate" in report
        assert "Sharpe" in report
