"""Tests for the orchestrator integration."""

from __future__ import annotations

import pytest

from hyperoil.config import AppConfig, EnvConfig
from hyperoil.core.orchestrator import Orchestrator
from hyperoil.types import (
    CycleStatus,
    Direction,
    Regime,
    SignalAction,
    SpreadSnapshot,
    StopReason,
    now_ms,
)


def _make_orchestrator() -> Orchestrator:
    config = AppConfig.model_validate({
        "execution": {"mode": "paper"},
        "observability": {"dashboard_enabled": False},
        "grid": {
            "entry_z": 1.5,
            "exit_z": 0.2,
            "stop_z": 4.5,
            "levels": [
                {"z": 1.5, "mult": 1.0},
                {"z": 2.0, "mult": 1.2},
            ],
        },
    })
    env = EnvConfig(hyperliquid_private_key="", hyperliquid_wallet_address="")
    return Orchestrator(config, env)


class TestOrchestratorInit:
    def test_creates_all_modules(self) -> None:
        orch = _make_orchestrator()
        assert orch._signal_engine is not None
        assert orch._decision_engine is not None
        assert orch._cycle_mgr is not None
        assert orch._kill_switch is not None
        assert orch._exposure is not None
        assert orch._risk_gate is not None
        assert orch._fill_tracker is not None

    def test_dashboard_disabled(self) -> None:
        orch = _make_orchestrator()
        assert orch._dashboard is None

    def test_dashboard_enabled(self) -> None:
        config = AppConfig.model_validate({
            "execution": {"mode": "paper"},
            "observability": {"dashboard_enabled": True},
        })
        env = EnvConfig()
        orch = Orchestrator(config, env)
        assert orch._dashboard is not None

    def test_initial_state(self) -> None:
        orch = _make_orchestrator()
        assert orch.state.daily_pnl == 0.0
        assert not orch.state.kill_switch_active
        assert not orch._cycle_mgr.has_open_cycle
