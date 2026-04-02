"""Shared test fixtures for HyperOil v2."""

from __future__ import annotations

import pytest

from hyperoil.config import AppConfig, load_config
from hyperoil.types import (
    CycleState,
    CycleStatus,
    Direction,
    GridLevel,
    Regime,
    SpreadSnapshot,
    Tick,
    now_ms,
)


@pytest.fixture
def app_config() -> AppConfig:
    """Load default config for tests."""
    return AppConfig()


@pytest.fixture
def sample_tick_cl() -> Tick:
    return Tick(
        timestamp_ms=now_ms(),
        symbol="CL",
        bid=68.50,
        ask=68.52,
        mid=68.51,
        last=68.51,
        volume=100.0,
    )


@pytest.fixture
def sample_tick_brent() -> Tick:
    return Tick(
        timestamp_ms=now_ms(),
        symbol="BRENTOIL",
        bid=72.30,
        ask=72.32,
        mid=72.31,
        last=72.31,
        volume=80.0,
    )


@pytest.fixture
def sample_spread_snapshot() -> SpreadSnapshot:
    return SpreadSnapshot(
        timestamp_ms=now_ms(),
        price_left=68.51,
        price_right=72.31,
        beta=0.95,
        spread=-0.0045,
        spread_mean=-0.0020,
        spread_std=0.0015,
        zscore=-1.67,
        correlation=0.92,
        vol_left=0.25,
        vol_right=0.23,
        regime=Regime.GOOD,
    )


@pytest.fixture
def sample_cycle_open() -> CycleState:
    ts = now_ms()
    return CycleState(
        cycle_id="test-cycle-001",
        status=CycleStatus.OPEN,
        direction=Direction.LONG_SPREAD,
        opened_at_ms=ts,
        last_action_ms=ts,
        levels=[
            GridLevel(
                level=1,
                z_entry=-1.67,
                z_current=-1.67,
                size_left=100.0,
                size_right=95.0,
                entry_price_left=68.51,
                entry_price_right=72.31,
                entry_beta=0.95,
                entry_timestamp_ms=ts,
                filled=True,
            )
        ],
        max_level_filled=1,
        entry_z_avg=-1.67,
        current_z=-1.67,
        total_size_left=100.0,
        total_size_right=95.0,
    )
