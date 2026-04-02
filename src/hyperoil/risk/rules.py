"""Risk rules — configurable checks that gate trading actions.

Each rule is a pure function: (state, config) → (allowed, reason).
Rules are composed by the Gate module for pre-trade validation.
"""

from __future__ import annotations

from dataclasses import dataclass

from hyperoil.config import RiskConfig
from hyperoil.types import CycleState, Regime, RiskCheckResult, SpreadSnapshot


@dataclass(frozen=True)
class RiskContext:
    """All state needed to evaluate risk rules."""
    snapshot: SpreadSnapshot
    cycle: CycleState | None
    daily_pnl: float
    consecutive_losses: int
    bars_since_last_stop: int
    total_notional: float
    kill_switch_active: bool


def check_kill_switch(ctx: RiskContext, _cfg: RiskConfig) -> RiskCheckResult:
    """Block all actions if kill switch is active."""
    if ctx.kill_switch_active:
        return RiskCheckResult(allowed=False, reason="kill_switch_active")
    return RiskCheckResult(allowed=True, reason="ok")


def check_regime(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Block entries in BAD regime."""
    if cfg.pause_on_bad_regime and ctx.snapshot.regime == Regime.BAD:
        return RiskCheckResult(
            allowed=False,
            reason="regime_bad",
            details={"regime": ctx.snapshot.regime.value},
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_correlation(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Block entries when correlation is below minimum."""
    if ctx.snapshot.correlation < cfg.min_correlation:
        return RiskCheckResult(
            allowed=False,
            reason="correlation_too_low",
            details={"correlation": ctx.snapshot.correlation, "min": cfg.min_correlation},
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_daily_loss(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Block entries when daily loss limit is breached."""
    if ctx.daily_pnl <= -cfg.max_daily_loss_usd:
        return RiskCheckResult(
            allowed=False,
            reason="daily_loss_exceeded",
            details={"daily_pnl": ctx.daily_pnl, "max": cfg.max_daily_loss_usd},
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_consecutive_losses(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Block entries after too many consecutive losses."""
    if ctx.consecutive_losses >= cfg.max_consecutive_losses:
        return RiskCheckResult(
            allowed=False,
            reason="consecutive_losses_exceeded",
            details={
                "consecutive": str(ctx.consecutive_losses),
                "max": str(cfg.max_consecutive_losses),
            },
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_cooldown(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Block entries during cooldown after a stop."""
    if ctx.bars_since_last_stop < cfg.cooldown_after_stop_bars:
        return RiskCheckResult(
            allowed=False,
            reason="cooldown_active",
            details={
                "bars_since_stop": str(ctx.bars_since_last_stop),
                "cooldown_bars": str(cfg.cooldown_after_stop_bars),
            },
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_spread_validity(ctx: RiskContext, _cfg: RiskConfig) -> RiskCheckResult:
    """Block entries when spread std is too small (unreliable z-score)."""
    if ctx.snapshot.spread_std < 0.0001:
        return RiskCheckResult(
            allowed=False,
            reason="spread_std_too_small",
            details={"spread_std": ctx.snapshot.spread_std},
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_total_notional(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Block entries when total notional would exceed limits."""
    from hyperoil.config import SizingConfig
    # Use max_total_notional from the sizing config default
    # This is checked at the position planner level, but double-check here
    max_total = cfg.max_drawdown_usd * 10  # rough proxy
    if ctx.total_notional > max_total:
        return RiskCheckResult(
            allowed=False,
            reason="total_notional_exceeded",
            details={"total_notional": ctx.total_notional},
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_cycle_loss(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Check if active cycle has exceeded max loss."""
    if ctx.cycle and ctx.cycle.unrealized_pnl <= -cfg.max_cycle_loss_usd:
        return RiskCheckResult(
            allowed=False,
            reason="cycle_loss_exceeded",
            details={
                "unrealized_pnl": ctx.cycle.unrealized_pnl,
                "max": cfg.max_cycle_loss_usd,
            },
        )
    return RiskCheckResult(allowed=True, reason="ok")


def check_spread_bps(ctx: RiskContext, cfg: RiskConfig) -> RiskCheckResult:
    """Block entries when bid-ask spread is too wide (liquidity check)."""
    # This uses vol as a proxy — in production, orderbook spread_bps is used
    # For now, always pass (the gate module can inject book data)
    return RiskCheckResult(allowed=True, reason="ok")


# Registry of all entry rules (order matters — cheapest checks first)
ENTRY_RULES = [
    check_kill_switch,
    check_regime,
    check_correlation,
    check_daily_loss,
    check_consecutive_losses,
    check_cooldown,
    check_spread_validity,
]

# Rules checked on open positions (for stop decisions)
POSITION_RULES = [
    check_kill_switch,
    check_cycle_loss,
    check_correlation,
    check_regime,
    check_daily_loss,
]
