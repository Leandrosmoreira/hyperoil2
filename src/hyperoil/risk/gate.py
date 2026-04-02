"""Pre-trade gate — validates every action against risk rules before execution.

The gate is the single enforcement point. If the gate says no, the action
does not happen. No exceptions.
"""

from __future__ import annotations

from hyperoil.config import RiskConfig
from hyperoil.observability.logger import get_logger
from hyperoil.risk.exposure import ExposureTracker
from hyperoil.risk.kill_switch import KillSwitch
from hyperoil.risk.rules import (
    ENTRY_RULES,
    POSITION_RULES,
    RiskContext,
)
from hyperoil.types import (
    CycleState,
    RiskCheckResult,
    SignalAction,
    SpreadSnapshot,
)

log = get_logger(__name__)


class RiskGate:
    """Pre-trade validation gate.

    Composes risk rules and exposure tracking to produce
    allow/deny decisions for every trading action.
    """

    def __init__(
        self,
        config: RiskConfig,
        exposure: ExposureTracker,
        kill_switch: KillSwitch,
    ) -> None:
        self._config = config
        self._exposure = exposure
        self._kill_switch = kill_switch

    def check_entry(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState | None = None,
    ) -> RiskCheckResult:
        """Validate whether a new entry is allowed.

        Runs all entry rules. First failure stops evaluation.
        """
        ctx = self._build_context(snapshot, cycle)

        for rule in ENTRY_RULES:
            result = rule(ctx, self._config)
            if not result.allowed:
                log.info(
                    "risk_gate_entry_blocked",
                    reason=result.reason,
                    details=result.details,
                )
                return result

        return RiskCheckResult(allowed=True, reason="ok")

    def check_add_level(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState,
    ) -> RiskCheckResult:
        """Validate whether adding a grid level is allowed.

        Uses the same entry rules — adding exposure is equivalent to entry.
        """
        return self.check_entry(snapshot, cycle)

    def check_position(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState,
    ) -> RiskCheckResult:
        """Check if an open position should be force-closed.

        Returns not-allowed with reason if position should be stopped.
        """
        ctx = self._build_context(snapshot, cycle)

        for rule in POSITION_RULES:
            result = rule(ctx, self._config)
            if not result.allowed:
                log.info(
                    "risk_gate_position_stop",
                    reason=result.reason,
                    details=result.details,
                )
                return result

        return RiskCheckResult(allowed=True, reason="ok")

    def check_action(
        self,
        action: SignalAction,
        snapshot: SpreadSnapshot,
        cycle: CycleState | None = None,
    ) -> RiskCheckResult:
        """Universal check — dispatches to the appropriate validator."""
        if action == SignalAction.ENTER:
            return self.check_entry(snapshot, cycle)
        elif action == SignalAction.ADD_LEVEL:
            if cycle is None:
                return RiskCheckResult(allowed=False, reason="no_active_cycle")
            return self.check_add_level(snapshot, cycle)
        elif action in (SignalAction.EXIT_FULL, SignalAction.EXIT_PARTIAL, SignalAction.STOP):
            # Exits and stops are always allowed — risk doesn't block de-risking
            return RiskCheckResult(allowed=True, reason="ok")
        elif action == SignalAction.HOLD:
            return RiskCheckResult(allowed=True, reason="ok")

        return RiskCheckResult(allowed=True, reason="ok")

    def is_system_healthy(self, snapshot: SpreadSnapshot) -> RiskCheckResult:
        """High-level system health check — can the bot operate at all?"""
        if self._kill_switch.is_active:
            return RiskCheckResult(
                allowed=False,
                reason="kill_switch_active",
                details={"source": self._kill_switch.reason or "unknown"},
            )

        if self._exposure.is_daily_loss_breached():
            return RiskCheckResult(
                allowed=False,
                reason="daily_loss_breached",
                details={"daily_pnl": self._exposure.daily_pnl},
            )

        if self._exposure.is_drawdown_breached():
            return RiskCheckResult(
                allowed=False,
                reason="drawdown_breached",
                details={
                    "drawdown_usd": self._exposure.drawdown_usd,
                    "drawdown_pct": self._exposure.drawdown_pct,
                },
            )

        return RiskCheckResult(allowed=True, reason="ok")

    def _build_context(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState | None,
    ) -> RiskContext:
        return RiskContext(
            snapshot=snapshot,
            cycle=cycle,
            daily_pnl=self._exposure.daily_pnl,
            consecutive_losses=self._exposure.consecutive_losses,
            bars_since_last_stop=self._exposure.bars_since_last_stop,
            total_notional=self._exposure.total_notional,
            kill_switch_active=self._kill_switch.is_active,
        )
