"""Performance metrics — computes risk-adjusted returns and trade statistics.

All metrics are computed from trade records and equity curves
produced by the simulator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from hyperoil.backtest.simulator import SimulationResult, TradeRecord


@dataclass(frozen=True)
class PerformanceMetrics:
    """Comprehensive backtest performance report."""
    # --- P&L ---
    total_net_pnl: float
    total_gross_pnl: float
    total_fees: float
    # --- Trade counts ---
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    # --- Returns ---
    avg_trade_pnl: float
    avg_winner: float
    avg_loser: float
    profit_factor: float
    expectancy: float
    # --- Drawdown ---
    max_drawdown_usd: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    # --- Risk-adjusted ---
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    # --- Trade detail ---
    avg_bars_held: float
    max_bars_held: int
    avg_levels_used: float
    max_levels_used: int
    # --- Per-regime (optional) ---
    trades_by_stop_reason: dict[str, int] = field(default_factory=dict)
    trades_by_direction: dict[str, int] = field(default_factory=dict)


def compute_metrics(result: SimulationResult) -> PerformanceMetrics:
    """Compute all performance metrics from a simulation result."""
    trades = result.trades
    equity = result.equity_curve

    if not trades:
        return _empty_metrics()

    # --- P&L ---
    net_pnls = [t.net_pnl for t in trades]
    gross_pnls = [t.gross_pnl for t in trades]
    fees = [t.fees for t in trades]

    total_net = sum(net_pnls)
    total_gross = sum(gross_pnls)
    total_fees = sum(fees)

    # --- Win/Loss ---
    winners = [p for p in net_pnls if p > 0]
    losers = [p for p in net_pnls if p <= 0]
    win_rate = len(winners) / len(trades) if trades else 0.0

    avg_winner = np.mean(winners) if winners else 0.0
    avg_loser = np.mean(losers) if losers else 0.0

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    expectancy = (win_rate * avg_winner) + ((1 - win_rate) * avg_loser)

    # --- Drawdown ---
    dd_usd, dd_pct, dd_duration = _compute_drawdown(equity)

    # --- Risk-adjusted ---
    sharpe = _compute_sharpe(net_pnls)
    sortino = _compute_sortino(net_pnls)
    calmar = total_net / dd_usd if dd_usd > 0 else float("inf")

    # --- Trade detail ---
    bars_held = [t.bars_held for t in trades]
    levels_used = [t.levels_used for t in trades]

    # --- Categorization ---
    by_reason: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    for t in trades:
        by_reason[t.stop_reason] = by_reason.get(t.stop_reason, 0) + 1
        by_direction[t.direction] = by_direction.get(t.direction, 0) + 1

    return PerformanceMetrics(
        total_net_pnl=round(total_net, 4),
        total_gross_pnl=round(total_gross, 4),
        total_fees=round(total_fees, 4),
        total_trades=len(trades),
        winning_trades=len(winners),
        losing_trades=len(losers),
        win_rate=round(win_rate, 4),
        avg_trade_pnl=round(float(np.mean(net_pnls)), 4),
        avg_winner=round(float(avg_winner), 4),
        avg_loser=round(float(avg_loser), 4),
        profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        expectancy=round(expectancy, 4),
        max_drawdown_usd=round(dd_usd, 4),
        max_drawdown_pct=round(dd_pct, 4),
        max_drawdown_duration_bars=dd_duration,
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        calmar_ratio=round(calmar, 4) if calmar != float("inf") else float("inf"),
        avg_bars_held=round(float(np.mean(bars_held)), 2) if bars_held else 0.0,
        max_bars_held=max(bars_held) if bars_held else 0,
        avg_levels_used=round(float(np.mean(levels_used)), 2) if levels_used else 0.0,
        max_levels_used=max(levels_used) if levels_used else 0,
        trades_by_stop_reason=by_reason,
        trades_by_direction=by_direction,
    )


def _compute_drawdown(equity: list[float]) -> tuple[float, float, int]:
    """Compute max drawdown in USD, percentage, and duration in bars.

    Returns (max_dd_usd, max_dd_pct, max_dd_duration).
    """
    if not equity:
        return 0.0, 0.0, 0

    arr = np.array(equity)
    peak = np.maximum.accumulate(arr)

    dd_usd_arr = peak - arr
    max_dd_usd = float(np.max(dd_usd_arr))

    # Percentage drawdown relative to peak
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_pct_arr = np.where(peak > 0, dd_usd_arr / peak, 0.0)
    max_dd_pct = float(np.max(dd_pct_arr))

    # Duration: longest streak below peak
    in_drawdown = dd_usd_arr > 0
    max_duration = 0
    current = 0
    for x in in_drawdown:
        if x:
            current += 1
            max_duration = max(max_duration, current)
        else:
            current = 0

    return max_dd_usd, max_dd_pct, max_duration


def _compute_sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio from per-trade returns."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - risk_free
    mean = float(np.mean(excess))
    std = float(np.std(excess, ddof=1))
    if std == 0:
        return 0.0
    # Annualize assuming ~252 trading days and ~4 trades/day (rough)
    trades_per_year = min(len(returns) * 4, 1000)
    return (mean / std) * math.sqrt(trades_per_year)


def _compute_sortino(returns: list[float], target: float = 0.0) -> float:
    """Sortino ratio — penalizes only downside deviation."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - target
    mean = float(np.mean(excess))

    downside = arr[arr < target] - target
    if len(downside) == 0:
        return float("inf") if mean > 0 else 0.0

    downside_std = float(np.std(downside, ddof=1))
    if downside_std == 0:
        return 0.0

    trades_per_year = min(len(returns) * 4, 1000)
    return (mean / downside_std) * math.sqrt(trades_per_year)


def _empty_metrics() -> PerformanceMetrics:
    return PerformanceMetrics(
        total_net_pnl=0.0,
        total_gross_pnl=0.0,
        total_fees=0.0,
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=0.0,
        avg_trade_pnl=0.0,
        avg_winner=0.0,
        avg_loser=0.0,
        profit_factor=0.0,
        expectancy=0.0,
        max_drawdown_usd=0.0,
        max_drawdown_pct=0.0,
        max_drawdown_duration_bars=0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        avg_bars_held=0.0,
        max_bars_held=0,
        avg_levels_used=0.0,
        max_levels_used=0,
    )


def format_report(metrics: PerformanceMetrics) -> str:
    """Format metrics as a human-readable report."""
    lines = [
        "=" * 60,
        "  BACKTEST PERFORMANCE REPORT",
        "=" * 60,
        "",
        f"  Total Net P&L:        ${metrics.total_net_pnl:>10.2f}",
        f"  Total Gross P&L:      ${metrics.total_gross_pnl:>10.2f}",
        f"  Total Fees:           ${metrics.total_fees:>10.2f}",
        "",
        f"  Total Trades:         {metrics.total_trades:>10d}",
        f"  Winners:              {metrics.winning_trades:>10d}",
        f"  Losers:               {metrics.losing_trades:>10d}",
        f"  Win Rate:             {metrics.win_rate:>10.1%}",
        "",
        f"  Avg Trade P&L:        ${metrics.avg_trade_pnl:>10.2f}",
        f"  Avg Winner:           ${metrics.avg_winner:>10.2f}",
        f"  Avg Loser:            ${metrics.avg_loser:>10.2f}",
        f"  Profit Factor:        {metrics.profit_factor:>10.2f}",
        f"  Expectancy:           ${metrics.expectancy:>10.2f}",
        "",
        f"  Max Drawdown (USD):   ${metrics.max_drawdown_usd:>10.2f}",
        f"  Max Drawdown (%):     {metrics.max_drawdown_pct:>10.2%}",
        f"  Max DD Duration:      {metrics.max_drawdown_duration_bars:>10d} bars",
        "",
        f"  Sharpe Ratio:         {metrics.sharpe_ratio:>10.2f}",
        f"  Sortino Ratio:        {metrics.sortino_ratio:>10.2f}",
        f"  Calmar Ratio:         {metrics.calmar_ratio:>10.2f}",
        "",
        f"  Avg Bars Held:        {metrics.avg_bars_held:>10.1f}",
        f"  Max Bars Held:        {metrics.max_bars_held:>10d}",
        f"  Avg Levels Used:      {metrics.avg_levels_used:>10.1f}",
        f"  Max Levels Used:      {metrics.max_levels_used:>10d}",
        "",
    ]

    if metrics.trades_by_stop_reason:
        lines.append("  Stop Reasons:")
        for reason, count in sorted(metrics.trades_by_stop_reason.items()):
            lines.append(f"    {reason:<25s} {count:>5d}")
        lines.append("")

    if metrics.trades_by_direction:
        lines.append("  Direction:")
        for direction, count in sorted(metrics.trades_by_direction.items()):
            lines.append(f"    {direction:<25s} {count:>5d}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)
