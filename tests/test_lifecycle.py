"""Tests for cycle lifecycle manager."""

from __future__ import annotations

import math

from hyperoil.config import GridConfig, GridLevelConfig, SizingConfig
from hyperoil.strategy.lifecycle import CycleManager
from hyperoil.types import (
    CycleStatus,
    Direction,
    Regime,
    SpreadSnapshot,
    StopReason,
    now_ms,
)


def _make_snapshot(
    zscore: float = -1.6,
    price_left: float = 68.50,
    price_right: float = 72.30,
    beta: float = 0.95,
) -> SpreadSnapshot:
    return SpreadSnapshot(
        timestamp_ms=now_ms(),
        price_left=price_left,
        price_right=price_right,
        beta=beta,
        spread=-0.004,
        spread_mean=-0.002,
        spread_std=0.002,
        zscore=zscore,
        correlation=0.85,
        vol_left=0.25,
        vol_right=0.23,
        regime=Regime.GOOD,
    )


def _default_config() -> tuple[SizingConfig, GridConfig]:
    return (
        SizingConfig(base_notional_usd=100.0, max_notional_per_cycle=1000.0),
        GridConfig(
            levels=[
                GridLevelConfig(z=1.5, mult=1.0),
                GridLevelConfig(z=2.0, mult=1.2),
                GridLevelConfig(z=2.5, mult=1.5),
            ],
            max_levels=3,
        ),
    )


class TestCycleManager:
    def test_open_cycle(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        assert not mgr.has_open_cycle

        cycle = mgr.open_cycle(
            direction=Direction.LONG_SPREAD,
            level=1,
            snapshot=_make_snapshot(zscore=-1.6),
            mult=1.0,
        )

        assert cycle is not None
        assert mgr.has_open_cycle
        assert cycle.status == CycleStatus.OPEN
        assert cycle.direction == Direction.LONG_SPREAD
        assert len(cycle.levels) == 1
        assert cycle.levels[0].filled
        assert cycle.max_level_filled == 1

    def test_cannot_open_twice(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(), 1.0)
        second = mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(), 1.0)
        assert second is None

    def test_add_level(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(zscore=-1.6), 1.0)
        level = mgr.add_level(2, _make_snapshot(zscore=-2.1), 1.2)

        assert level is not None
        assert level.level == 2
        cycle = mgr.active_cycle
        assert cycle is not None
        assert len(cycle.levels) == 2
        assert cycle.max_level_filled == 2

    def test_update_pnl(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        # Open LONG_SPREAD at z=-1.6
        snap_entry = _make_snapshot(zscore=-1.6, price_left=68.50, price_right=72.30)
        mgr.open_cycle(Direction.LONG_SPREAD, 1, snap_entry, 1.0)

        # Price moves favorably: CL up, BRENT stays
        snap_update = _make_snapshot(zscore=-0.8, price_left=69.50, price_right=72.30)
        mgr.update(snap_update)

        cycle = mgr.active_cycle
        assert cycle is not None
        assert cycle.current_z == -0.8
        # P&L should be positive (CL went up in long spread)
        assert cycle.unrealized_pnl > 0

    def test_update_adverse_tracking(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(zscore=-1.6), 1.0)

        # Z goes more negative (adverse for long spread)
        mgr.update(_make_snapshot(zscore=-2.5))
        cycle = mgr.active_cycle
        assert cycle is not None
        assert cycle.peak_adverse_z == -2.5

        # Z recovers (favorable)
        mgr.update(_make_snapshot(zscore=-0.5))
        assert cycle.peak_favorable_z == -0.5

    def test_close_cycle_take_profit(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(), 1.0)
        closed = mgr.close_cycle(StopReason.TAKE_PROFIT, z_exit=-0.1)

        assert closed is not None
        assert closed.status == CycleStatus.CLOSED
        assert closed.stop_reason == StopReason.TAKE_PROFIT
        assert not mgr.has_open_cycle

    def test_close_cycle_stop(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(), 1.0)
        closed = mgr.close_cycle(StopReason.STOP_LOSS_Z, z_exit=-4.6)

        assert closed is not None
        assert closed.status == CycleStatus.STOPPED
        assert closed.stop_reason == StopReason.STOP_LOSS_Z

    def test_force_close(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(), 1.0)
        closed = mgr.force_close()

        assert closed is not None
        assert closed.stop_reason == StopReason.KILL_SWITCH
        assert not mgr.has_open_cycle

    def test_existing_notional(self) -> None:
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        assert mgr.existing_notional == 0.0

        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(), 1.0)
        assert mgr.existing_notional > 0.0

    def test_pnl_direction_long_spread(self) -> None:
        """LONG_SPREAD: long CL, short BRENT. CL up = profit."""
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        snap_entry = _make_snapshot(price_left=68.50, price_right=72.30)
        mgr.open_cycle(Direction.LONG_SPREAD, 1, snap_entry, 1.0)

        # CL goes up 1%, BRENT stays flat
        mgr.update(_make_snapshot(price_left=69.185, price_right=72.30))
        assert mgr.active_cycle is not None
        assert mgr.active_cycle.unrealized_pnl > 0

    def test_pnl_direction_short_spread(self) -> None:
        """SHORT_SPREAD: short CL, long BRENT. CL down = profit."""
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        snap_entry = _make_snapshot(zscore=1.8, price_left=68.50, price_right=72.30)
        mgr.open_cycle(Direction.SHORT_SPREAD, 1, snap_entry, 1.0)

        # CL goes down 1%, BRENT stays flat
        mgr.update(_make_snapshot(price_left=67.815, price_right=72.30))
        assert mgr.active_cycle is not None
        assert mgr.active_cycle.unrealized_pnl > 0

    def test_full_cycle_lifecycle(self) -> None:
        """Complete cycle: open → add → update → close."""
        sizing, grid = _default_config()
        mgr = CycleManager(sizing, grid)

        # 1. Open
        mgr.open_cycle(Direction.LONG_SPREAD, 1, _make_snapshot(zscore=-1.6), 1.0)
        assert mgr.has_open_cycle

        # 2. Update (z deepens)
        mgr.update(_make_snapshot(zscore=-2.0))

        # 3. Add level 2
        mgr.add_level(2, _make_snapshot(zscore=-2.1), 1.2)
        assert mgr.active_cycle is not None
        assert len(mgr.active_cycle.levels) == 2

        # 4. Update (z reverts)
        mgr.update(_make_snapshot(zscore=-0.5, price_left=69.50, price_right=72.30))

        # 5. Close
        closed = mgr.close_cycle(StopReason.TAKE_PROFIT, z_exit=-0.1)
        assert closed is not None
        assert closed.max_level_filled == 2
        assert not mgr.has_open_cycle
