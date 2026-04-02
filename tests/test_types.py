"""Tests for shared types and dataclasses."""

from __future__ import annotations

from hyperoil.types import (
    CycleState,
    CycleStatus,
    Direction,
    GridLevel,
    OrderSide,
    OrderStatus,
    Regime,
    RiskCheckResult,
    StopReason,
    now_ms,
)


def test_direction_values() -> None:
    assert Direction.LONG_SPREAD.value == "long_spread"
    assert Direction.SHORT_SPREAD.value == "short_spread"


def test_cycle_status_transitions() -> None:
    cycle = CycleState(cycle_id="test-001")
    assert cycle.status == CycleStatus.IDLE

    cycle.status = CycleStatus.OPENING
    assert cycle.status == CycleStatus.OPENING

    cycle.status = CycleStatus.OPEN
    assert cycle.status == CycleStatus.OPEN


def test_risk_check_result() -> None:
    allowed = RiskCheckResult(allowed=True, reason="ok")
    assert allowed.allowed

    blocked = RiskCheckResult(
        allowed=False,
        reason="max_daily_loss_exceeded",
        details={"daily_pnl": -310.0, "limit": -300.0},
    )
    assert not blocked.allowed
    assert blocked.details["daily_pnl"] == -310.0


def test_now_ms_is_positive() -> None:
    ts = now_ms()
    assert ts > 0
    assert isinstance(ts, int)


def test_grid_level_defaults() -> None:
    level = GridLevel(
        level=1,
        z_entry=1.5,
        z_current=1.5,
        size_left=100.0,
        size_right=95.0,
        entry_price_left=68.0,
        entry_price_right=72.0,
        entry_beta=0.95,
        entry_timestamp_ms=now_ms(),
    )
    assert level.mae_z == 0.0
    assert level.mfe_z == 0.0
    assert level.bars_held == 0
    assert not level.filled
