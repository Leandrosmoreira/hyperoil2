"""Performance metrics for the Donchian backtest.

Metrics computed from the equity curve produced by the simulator (NOT from
trades — equity-curve metrics are the right denomination for a portfolio
strategy because trades overlap and per-trade aggregation is misleading).

All return-based statistics use 4h-bar log returns of the equity curve and
are annualized assuming 6 bars/day × 365 days = 2190 periods/year.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from hyperoil.donchian.backtest.simulator import SimulationResult

# Same convention as the realized-vol module — 4h candles, 365 calendar days.
PERIODS_PER_YEAR_4H = 6 * 365  # 2190
MS_PER_YEAR = 365.25 * 24 * 3600 * 1000


@dataclass(frozen=True)
class DonchianMetrics:
    # P&L
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr: float
    # Risk-adjusted
    sharpe: float
    sortino: float
    calmar: float
    # Drawdown
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    # Trades
    n_trades: int
    n_winning_trades: int
    n_losing_trades: int
    win_rate: float
    profit_factor: float
    # Fees / costs
    total_fees: float
    total_realized_pnl: float
    # Per-asset breakdown
    per_asset_pnl: dict[str, float] = field(default_factory=dict)
    per_asset_trades: dict[str, int] = field(default_factory=dict)


def compute_donchian_metrics(result: SimulationResult) -> DonchianMetrics:
    """Compute the full DonchianMetrics report from a SimulationResult."""
    equity = np.asarray(result.equity_curve, dtype=float)
    if len(equity) < 2:
        return _empty(result)

    initial = result.initial_capital
    final = result.final_equity
    total_return = final / initial - 1.0

    # Bar-level log returns of the equity curve.
    log_rets = np.diff(np.log(np.where(equity > 0, equity, np.nan)))
    log_rets = log_rets[np.isfinite(log_rets)]

    sharpe = _annualized_sharpe(log_rets)
    sortino = _annualized_sortino(log_rets)

    # Max drawdown — recompute from the equity curve directly so the metric
    # is independent of any DD tracked inside the portfolio manager.
    max_dd_pct, max_dd_duration = _max_drawdown(equity)

    # CAGR — derive elapsed years from the timestamps; fall back to bars/2190
    # if timestamps are missing.
    if result.timestamps and len(result.timestamps) >= 2:
        years = (result.timestamps[-1] - result.timestamps[0]) / MS_PER_YEAR
    else:
        years = max(1.0, len(equity) / PERIODS_PER_YEAR_4H)
    cagr = (final / initial) ** (1.0 / years) - 1.0 if years > 0 and initial > 0 else 0.0
    calmar = cagr / max_dd_pct if max_dd_pct > 0 else float("inf")

    # Trade-level stats — count only EXIT/DECREASE actions that booked PnL.
    realized_trades = [t for t in result.trades if t.realized_pnl != 0.0]
    winners = [t for t in realized_trades if t.realized_pnl > 0]
    losers = [t for t in realized_trades if t.realized_pnl < 0]
    win_rate = len(winners) / len(realized_trades) if realized_trades else 0.0

    gross_profit = sum(t.realized_pnl for t in winners)
    gross_loss = abs(sum(t.realized_pnl for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_fees = sum(t.fee for t in result.trades)
    total_realized = sum(t.realized_pnl for t in result.trades)

    return DonchianMetrics(
        initial_capital=round(initial, 4),
        final_equity=round(final, 4),
        total_return_pct=round(total_return, 6),
        cagr=round(cagr, 6),
        sharpe=round(sharpe, 4),
        sortino=round(sortino, 4),
        calmar=round(calmar, 4) if math.isfinite(calmar) else float("inf"),
        max_drawdown_pct=round(max_dd_pct, 6),
        max_drawdown_duration_bars=int(max_dd_duration),
        n_trades=len(realized_trades),
        n_winning_trades=len(winners),
        n_losing_trades=len(losers),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 4) if math.isfinite(profit_factor) else float("inf"),
        total_fees=round(total_fees, 4),
        total_realized_pnl=round(total_realized, 4),
        per_asset_pnl={k: round(v, 4) for k, v in result.per_asset_pnl.items()},
        per_asset_trades=dict(result.per_asset_trades),
    )


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _annualized_sharpe(log_rets: np.ndarray) -> float:
    if len(log_rets) < 2:
        return 0.0
    mean = float(np.mean(log_rets))
    std = float(np.std(log_rets, ddof=1))
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(PERIODS_PER_YEAR_4H)


def _annualized_sortino(log_rets: np.ndarray) -> float:
    if len(log_rets) < 2:
        return 0.0
    mean = float(np.mean(log_rets))
    downside = log_rets[log_rets < 0]
    if len(downside) == 0:
        return float("inf") if mean > 0 else 0.0
    dstd = float(np.std(downside, ddof=1))
    if dstd == 0:
        return 0.0
    return (mean / dstd) * math.sqrt(PERIODS_PER_YEAR_4H)


def _max_drawdown(equity: np.ndarray) -> tuple[float, int]:
    """Returns (max_dd_pct, max_dd_duration_bars)."""
    peak = np.maximum.accumulate(equity)
    dd = np.where(peak > 0, (peak - equity) / peak, 0.0)
    max_dd = float(np.max(dd))

    in_dd = dd > 0
    max_dur = 0
    cur = 0
    for x in in_dd:
        if x:
            cur += 1
            max_dur = max(max_dur, cur)
        else:
            cur = 0
    return max_dd, max_dur


def _empty(result: SimulationResult) -> DonchianMetrics:
    return DonchianMetrics(
        initial_capital=result.initial_capital,
        final_equity=result.final_equity,
        total_return_pct=0.0,
        cagr=0.0,
        sharpe=0.0,
        sortino=0.0,
        calmar=0.0,
        max_drawdown_pct=0.0,
        max_drawdown_duration_bars=0,
        n_trades=0,
        n_winning_trades=0,
        n_losing_trades=0,
        win_rate=0.0,
        profit_factor=0.0,
        total_fees=0.0,
        total_realized_pnl=0.0,
    )


def format_donchian_report(m: DonchianMetrics) -> str:
    lines = [
        "=" * 60,
        "  DONCHIAN ENSEMBLE — BACKTEST REPORT",
        "=" * 60,
        f"  Initial Capital:        ${m.initial_capital:>14,.2f}",
        f"  Final Equity:           ${m.final_equity:>14,.2f}",
        f"  Total Return:           {m.total_return_pct * 100:>14,.2f} %",
        f"  CAGR:                   {m.cagr * 100:>14,.2f} %",
        "",
        f"  Sharpe (4h-ret):        {m.sharpe:>14,.3f}",
        f"  Sortino:                {m.sortino:>14,.3f}",
        f"  Calmar:                 {m.calmar:>14,.3f}",
        "",
        f"  Max Drawdown:           {m.max_drawdown_pct * 100:>14,.2f} %",
        f"  Max DD Duration:        {m.max_drawdown_duration_bars:>14d} bars",
        "",
        f"  Trades (closed):        {m.n_trades:>14d}",
        f"  Winners / Losers:       {m.n_winning_trades:>7d} / {m.n_losing_trades:<7d}",
        f"  Win Rate:               {m.win_rate * 100:>14,.2f} %",
        f"  Profit Factor:          {m.profit_factor:>14,.3f}",
        "",
        f"  Total Realized PnL:     ${m.total_realized_pnl:>14,.2f}",
        f"  Total Fees:             ${m.total_fees:>14,.2f}",
        "",
    ]
    if m.per_asset_pnl:
        lines.append("  Per-Asset PnL:")
        for sym, pnl in sorted(m.per_asset_pnl.items(), key=lambda kv: -kv[1]):
            n = m.per_asset_trades.get(sym, 0)
            lines.append(f"    {sym:<20s} ${pnl:>+12,.2f}   ({n} trades)")
        lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
