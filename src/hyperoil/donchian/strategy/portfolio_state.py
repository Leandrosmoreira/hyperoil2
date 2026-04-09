"""Mutable portfolio state held by PortfolioManager.

Distinct from `PortfolioSnapshot` (in `hyperoil.donchian.types`) which is the
immutable point-in-time view returned for telemetry / persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperoil.donchian.types import DonchianPosition


@dataclass
class PortfolioState:
    """Live, mutable bookkeeping for the Donchian portfolio.

    Equity model:
        equity      = cash + Σ unrealized_pnl
        cash         decreases by fees, increases by realized_pnl on close
        peak_equity  = max(peak_equity, equity) — never decreases
        drawdown_pct = max(0, (peak_equity - equity) / peak_equity)

    Margin is NOT tracked as a separate bucket. Each position carries its
    notional `size_usd` and `leverage` for telemetry; the orchestrator
    enforces total-leverage caps at a higher level.
    """

    cash: float
    equity: float
    peak_equity: float
    drawdown_pct: float
    timestamp_ms: int = 0
    positions: dict[str, DonchianPosition] = field(default_factory=dict)
    realized_pnl_total: float = 0.0
    fees_paid_total: float = 0.0
    n_trades_total: int = 0
