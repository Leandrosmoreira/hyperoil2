"""Grid pair trading logic — entry, exit, add, stop decisions based on z-score.

Core decision engine: given current state + signals, returns what action to take.
Stateless — all state comes from CycleState and SpreadSnapshot.
"""

from __future__ import annotations

from hyperoil.config import GridConfig, RiskConfig
from hyperoil.observability.logger import get_logger
from hyperoil.types import (
    CycleState,
    CycleStatus,
    Direction,
    Regime,
    SignalAction,
    SpreadSnapshot,
    StopReason,
)

log = get_logger(__name__)


class GridDecisionEngine:
    """Decides trading actions based on z-score grid levels.

    Pure logic — no side effects, no I/O.
    """

    def __init__(self, grid: GridConfig, risk: RiskConfig) -> None:
        self._grid = grid
        self._risk = risk

    def evaluate(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState | None,
        bars_since_last_stop: int,
        consecutive_losses: int,
        daily_pnl: float,
        kill_switch: bool,
    ) -> tuple[SignalAction, dict]:
        """Evaluate current state and return recommended action.

        Returns:
            (action, details) where details contains context for the action.
        """
        z = snapshot.zscore

        # --- Global blocks ---
        if kill_switch:
            if cycle and cycle.status == CycleStatus.OPEN:
                return SignalAction.EXIT_FULL, {"reason": StopReason.KILL_SWITCH}
            return SignalAction.HOLD, {"reason": "kill_switch_active"}

        # --- Stop checks on open cycle ---
        if cycle and cycle.status == CycleStatus.OPEN:
            stop = self._check_stops(snapshot, cycle, daily_pnl)
            if stop:
                return stop

            # Check exit (take profit)
            exit_action = self._check_exit(snapshot, cycle)
            if exit_action:
                return exit_action

            # Check add level
            add_action = self._check_add(snapshot, cycle, bars_since_last_stop)
            if add_action:
                return add_action

            return SignalAction.HOLD, {"zscore": z}

        # --- Entry check (no open cycle) ---
        if cycle is None or cycle.status in (CycleStatus.IDLE, CycleStatus.CLOSED, CycleStatus.STOPPED):
            entry = self._check_entry(
                snapshot, bars_since_last_stop, consecutive_losses, daily_pnl,
            )
            if entry:
                return entry

        return SignalAction.HOLD, {"zscore": z}

    def _check_entry(
        self,
        snapshot: SpreadSnapshot,
        bars_since_last_stop: int,
        consecutive_losses: int,
        daily_pnl: float,
    ) -> tuple[SignalAction, dict] | None:
        """Check if we should open a new cycle."""
        z = snapshot.zscore

        # Regime gate
        if self._risk.pause_on_bad_regime and snapshot.regime == Regime.BAD:
            return None

        # Correlation gate
        if snapshot.correlation < self._risk.min_correlation:
            return None

        # Daily loss gate
        if daily_pnl <= -self._risk.max_daily_loss_usd:
            return None

        # Consecutive losses cooldown
        if consecutive_losses >= self._risk.max_consecutive_losses:
            return None

        # Cooldown after stop
        if bars_since_last_stop < self._risk.cooldown_after_stop_bars:
            return None

        # Std validity
        if snapshot.spread_std < 0.0001:
            return None

        # Check first grid level
        if not self._grid.levels:
            return None

        first_level = self._grid.levels[0]

        if abs(z) >= first_level.z:
            direction = Direction.SHORT_SPREAD if z > 0 else Direction.LONG_SPREAD
            return SignalAction.ENTER, {
                "direction": direction,
                "level": 1,
                "z_entry": z,
                "mult": first_level.mult,
                "beta": snapshot.beta,
                "price_left": snapshot.price_left,
                "price_right": snapshot.price_right,
                "regime": snapshot.regime,
            }

        return None

    def _check_exit(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState,
    ) -> tuple[SignalAction, dict] | None:
        """Check take-profit exit."""
        z = snapshot.zscore
        exit_z = self._grid.exit_z

        if cycle.direction == Direction.SHORT_SPREAD:
            # Entered when z was high positive — exit when z falls back
            if z <= exit_z:
                return SignalAction.EXIT_FULL, {
                    "reason": StopReason.TAKE_PROFIT,
                    "z_exit": z,
                }
        elif cycle.direction == Direction.LONG_SPREAD:
            # Entered when z was negative — exit when z rises back
            if z >= -exit_z:
                return SignalAction.EXIT_FULL, {
                    "reason": StopReason.TAKE_PROFIT,
                    "z_exit": z,
                }

        return None

    def _check_add(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState,
        bars_since_last_action: int,
    ) -> tuple[SignalAction, dict] | None:
        """Check if we should add a grid level."""
        z = snapshot.zscore
        current_levels = cycle.max_level_filled

        if current_levels >= self._grid.max_levels:
            return None

        if current_levels >= len(self._grid.levels):
            return None

        # Cooldown between adds
        if bars_since_last_action < self._grid.cooldown_bars:
            return None

        next_level_cfg = self._grid.levels[current_levels]  # 0-indexed, levels[0] is L1

        # For SHORT_SPREAD: z should go even more positive to add
        # For LONG_SPREAD: z should go even more negative to add
        if cycle.direction == Direction.SHORT_SPREAD:
            if z >= next_level_cfg.z:
                return SignalAction.ADD_LEVEL, {
                    "level": current_levels + 1,
                    "z_entry": z,
                    "mult": next_level_cfg.mult,
                    "beta": snapshot.beta,
                    "price_left": snapshot.price_left,
                    "price_right": snapshot.price_right,
                }
        elif cycle.direction == Direction.LONG_SPREAD:
            if z <= -next_level_cfg.z:
                return SignalAction.ADD_LEVEL, {
                    "level": current_levels + 1,
                    "z_entry": z,
                    "mult": next_level_cfg.mult,
                    "beta": snapshot.beta,
                    "price_left": snapshot.price_left,
                    "price_right": snapshot.price_right,
                }

        return None

    def _check_stops(
        self,
        snapshot: SpreadSnapshot,
        cycle: CycleState,
        daily_pnl: float,
    ) -> tuple[SignalAction, dict] | None:
        """Check all stop conditions."""
        z = snapshot.zscore

        # 1. Stop by z-score extreme
        if abs(z) >= self._grid.stop_z:
            return SignalAction.STOP, {
                "reason": StopReason.STOP_LOSS_Z,
                "z_exit": z,
            }

        # 2. Stop by cycle monetary loss
        if cycle.unrealized_pnl <= -self._risk.max_cycle_loss_usd:
            return SignalAction.STOP, {
                "reason": StopReason.STOP_LOSS_MONETARY,
                "cycle_pnl": cycle.unrealized_pnl,
            }

        # 3. Stop by time
        if cycle.opened_at_ms > 0:
            from hyperoil.types import now_ms
            elapsed_min = (now_ms() - cycle.opened_at_ms) / 60000
            if elapsed_min >= self._risk.max_cycle_minutes:
                return SignalAction.STOP, {
                    "reason": StopReason.STOP_TIME,
                    "elapsed_min": elapsed_min,
                }

        # 4. Stop by correlation break
        if snapshot.correlation < self._risk.min_correlation:
            return SignalAction.STOP, {
                "reason": StopReason.CORRELATION_BREAK,
                "correlation": snapshot.correlation,
            }

        # 5. Stop by regime change
        if self._risk.pause_on_bad_regime and snapshot.regime == Regime.BAD:
            return SignalAction.STOP, {
                "reason": StopReason.REGIME_CHANGE,
                "regime": snapshot.regime.value,
            }

        # 6. Stop by daily loss
        if daily_pnl <= -self._risk.max_daily_loss_usd:
            return SignalAction.STOP, {
                "reason": StopReason.STOP_LOSS_MONETARY,
                "daily_pnl": daily_pnl,
            }

        # 7. Stop by MAE
        peak_adverse = abs(cycle.peak_adverse_z)
        if peak_adverse >= self._risk.max_mae_z:
            return SignalAction.STOP, {
                "reason": StopReason.MAX_MAE,
                "mae_z": peak_adverse,
            }

        return None
