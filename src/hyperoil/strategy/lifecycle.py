"""Cycle lifecycle manager — owns CycleState transitions and P&L tracking.

Manages the full lifecycle: IDLE → OPENING → OPEN → ADDING → REDUCING → CLOSING → CLOSED/STOPPED
"""

from __future__ import annotations

import math
import uuid

from hyperoil.config import GridConfig, SizingConfig
from hyperoil.observability.logger import get_logger
from hyperoil.strategy.position_plan import LegSizes, PositionPlanner
from hyperoil.types import (
    CycleState,
    CycleStatus,
    Direction,
    GridLevel,
    SpreadSnapshot,
    StopReason,
    now_ms,
)

log = get_logger(__name__)


class CycleManager:
    """Manages the lifecycle of a single trading cycle."""

    def __init__(self, sizing: SizingConfig, grid: GridConfig) -> None:
        self._planner = PositionPlanner(sizing, grid)
        self._cycle: CycleState | None = None

    @property
    def active_cycle(self) -> CycleState | None:
        return self._cycle

    @property
    def has_open_cycle(self) -> bool:
        return self._cycle is not None and self._cycle.status in (
            CycleStatus.OPEN, CycleStatus.OPENING, CycleStatus.ADDING,
            CycleStatus.REDUCING, CycleStatus.CLOSING,
        )

    @property
    def existing_notional(self) -> float:
        """Total notional currently deployed."""
        if not self._cycle:
            return 0.0
        return sum(
            lv.size_left * lv.entry_price_left + lv.size_right * lv.entry_price_right
            for lv in self._cycle.levels
            if lv.filled
        )

    def open_cycle(
        self,
        direction: Direction,
        level: int,
        snapshot: SpreadSnapshot,
        mult: float,
    ) -> CycleState | None:
        """Open a new trading cycle at the first grid level.

        Returns the CycleState if sizes are valid, None if rejected.
        """
        if self.has_open_cycle:
            log.warning("lifecycle_open_rejected_already_active")
            return None

        sizes = self._planner.compute_sizes(
            level=level,
            beta=snapshot.beta,
            price_left=snapshot.price_left,
            price_right=snapshot.price_right,
        )
        if sizes is None:
            return None

        ts = now_ms()
        cycle_id = f"cycle-{uuid.uuid4().hex[:12]}"

        grid_level = GridLevel(
            level=level,
            z_entry=snapshot.zscore,
            z_current=snapshot.zscore,
            size_left=sizes.size_left,
            size_right=sizes.size_right,
            entry_price_left=snapshot.price_left,
            entry_price_right=snapshot.price_right,
            entry_beta=snapshot.beta,
            entry_timestamp_ms=ts,
            filled=True,
        )

        self._cycle = CycleState(
            cycle_id=cycle_id,
            status=CycleStatus.OPEN,
            direction=direction,
            opened_at_ms=ts,
            last_action_ms=ts,
            levels=[grid_level],
            max_level_filled=level,
            entry_z_avg=snapshot.zscore,
            current_z=snapshot.zscore,
            peak_adverse_z=snapshot.zscore,
            peak_favorable_z=snapshot.zscore,
            total_size_left=sizes.size_left,
            total_size_right=sizes.size_right,
        )

        log.info(
            "cycle_opened",
            cycle_id=cycle_id,
            direction=direction.value,
            level=level,
            zscore=round(snapshot.zscore, 4),
            size_left=sizes.size_left,
            size_right=sizes.size_right,
        )

        return self._cycle

    def add_level(
        self,
        level: int,
        snapshot: SpreadSnapshot,
        mult: float,
    ) -> GridLevel | None:
        """Add a new grid level to the active cycle.

        Returns the new GridLevel if successful, None if rejected.
        """
        if not self._cycle or self._cycle.status != CycleStatus.OPEN:
            return None

        sizes = self._planner.compute_sizes(
            level=level,
            beta=snapshot.beta,
            price_left=snapshot.price_left,
            price_right=snapshot.price_right,
            existing_notional=self.existing_notional,
        )
        if sizes is None:
            return None

        ts = now_ms()
        grid_level = GridLevel(
            level=level,
            z_entry=snapshot.zscore,
            z_current=snapshot.zscore,
            size_left=sizes.size_left,
            size_right=sizes.size_right,
            entry_price_left=snapshot.price_left,
            entry_price_right=snapshot.price_right,
            entry_beta=snapshot.beta,
            entry_timestamp_ms=ts,
            filled=True,
        )

        self._cycle.levels.append(grid_level)
        self._cycle.max_level_filled = max(self._cycle.max_level_filled, level)
        self._cycle.last_action_ms = ts
        self._cycle.total_size_left += sizes.size_left
        self._cycle.total_size_right += sizes.size_right

        # Recalculate weighted average entry z
        total_notional = sum(lv.size_left * lv.entry_price_left for lv in self._cycle.levels)
        if total_notional > 0:
            self._cycle.entry_z_avg = sum(
                lv.z_entry * (lv.size_left * lv.entry_price_left)
                for lv in self._cycle.levels
            ) / total_notional

        log.info(
            "level_added",
            cycle_id=self._cycle.cycle_id,
            level=level,
            zscore=round(snapshot.zscore, 4),
            total_levels=len(self._cycle.levels),
        )

        return grid_level

    def update(self, snapshot: SpreadSnapshot) -> None:
        """Update cycle state with new market data (call each bar)."""
        if not self._cycle or self._cycle.status not in (CycleStatus.OPEN, CycleStatus.ADDING):
            return

        z = snapshot.zscore
        self._cycle.current_z = z

        # Track peak adverse/favorable
        if self._cycle.direction == Direction.SHORT_SPREAD:
            # Adverse = z going higher (more positive)
            if z > self._cycle.peak_adverse_z:
                self._cycle.peak_adverse_z = z
            # Favorable = z falling back toward zero
            if z < self._cycle.peak_favorable_z:
                self._cycle.peak_favorable_z = z
        else:
            # LONG_SPREAD: adverse = z going more negative
            if z < self._cycle.peak_adverse_z:
                self._cycle.peak_adverse_z = z
            # Favorable = z rising back toward zero
            if z > self._cycle.peak_favorable_z:
                self._cycle.peak_favorable_z = z

        # Update P&L and per-level tracking
        total_unrealized = 0.0
        for lv in self._cycle.levels:
            if not lv.filled:
                continue

            lv.z_current = z
            lv.bars_held += 1

            # P&L based on log-spread delta
            pnl = self._compute_level_pnl(
                lv, snapshot.price_left, snapshot.price_right, self._cycle.direction,
            )
            lv.realized_pnl = pnl
            total_unrealized += pnl

            # Update MAE/MFE (as absolute z-score distance from entry)
            if self._cycle.direction == Direction.SHORT_SPREAD:
                adverse = max(0, z - lv.z_entry)  # z going higher is adverse
                favorable = max(0, lv.z_entry - z)  # z going lower is favorable
            else:
                adverse = max(0, lv.z_entry - z)  # z going more negative is adverse
                favorable = max(0, z - lv.z_entry)  # z going up is favorable

            lv.mae_z = max(lv.mae_z, adverse)
            lv.mfe_z = max(lv.mfe_z, favorable)

        self._cycle.unrealized_pnl = total_unrealized

    def close_cycle(self, reason: StopReason, z_exit: float = 0.0) -> CycleState | None:
        """Close the active cycle. Returns the closed cycle for recording."""
        if not self._cycle:
            return None

        ts = now_ms()
        self._cycle.status = CycleStatus.CLOSED if reason == StopReason.TAKE_PROFIT else CycleStatus.STOPPED
        self._cycle.stop_reason = reason
        self._cycle.closed_at_ms = ts
        self._cycle.realized_pnl = self._cycle.unrealized_pnl

        closed = self._cycle

        log.info(
            "cycle_closed",
            cycle_id=closed.cycle_id,
            reason=reason.value,
            levels=closed.max_level_filled,
            pnl=round(closed.realized_pnl, 2),
            z_entry_avg=round(closed.entry_z_avg, 4),
            z_exit=round(z_exit, 4),
            bars_held=sum(lv.bars_held for lv in closed.levels),
        )

        self._cycle = None
        return closed

    def force_close(self) -> CycleState | None:
        """Emergency close — used by kill switch."""
        return self.close_cycle(StopReason.KILL_SWITCH)

    @staticmethod
    def _compute_level_pnl(
        level: GridLevel,
        price_left_now: float,
        price_right_now: float,
        direction: Direction,
    ) -> float:
        """Compute unrealized P&L for a grid level.

        Uses log-spread delta method:
            LONG_SPREAD: pnl = notional * (CL_return) - notional * beta * (BRENT_return)
            SHORT_SPREAD: pnl = -notional * (CL_return) + notional * beta * (BRENT_return)
        """
        if level.entry_price_left <= 0 or level.entry_price_right <= 0:
            return 0.0

        cl_return = math.log(price_left_now / level.entry_price_left)
        brent_return = math.log(price_right_now / level.entry_price_right)
        beta = level.entry_beta
        notional = level.size_left * level.entry_price_left

        if direction == Direction.LONG_SPREAD:
            return notional * cl_return - notional * beta * brent_return
        else:
            return -notional * cl_return + notional * beta * brent_return
