"""Tests for the risk gate (pre-trade validation)."""

from __future__ import annotations

from hyperoil.config import RiskConfig
from hyperoil.risk.exposure import ExposureTracker
from hyperoil.risk.gate import RiskGate
from hyperoil.risk.kill_switch import KillSwitch
from hyperoil.types import (
    CycleState,
    CycleStatus,
    Regime,
    RiskCheckResult,
    SignalAction,
    SpreadSnapshot,
    now_ms,
)


def _snap(
    regime: Regime = Regime.GOOD,
    correlation: float = 0.85,
    spread_std: float = 0.002,
    zscore: float = -1.6,
) -> SpreadSnapshot:
    return SpreadSnapshot(
        timestamp_ms=now_ms(),
        price_left=68.50,
        price_right=72.30,
        beta=0.95,
        spread=-0.004,
        spread_mean=-0.002,
        spread_std=spread_std,
        zscore=zscore,
        correlation=correlation,
        vol_left=0.25,
        vol_right=0.23,
        regime=regime,
    )


def _gate(tmp_path, config: RiskConfig | None = None) -> RiskGate:
    cfg = config or RiskConfig()
    exposure = ExposureTracker(cfg)
    ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
    return RiskGate(config=cfg, exposure=exposure, kill_switch=ks)


class TestRiskGateEntry:
    def test_allows_normal_entry(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.check_entry(_snap())
        assert result.allowed

    def test_blocks_bad_regime(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.check_entry(_snap(regime=Regime.BAD))
        assert not result.allowed
        assert result.reason == "regime_bad"

    def test_blocks_low_correlation(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.check_entry(_snap(correlation=0.45))
        assert not result.allowed
        assert result.reason == "correlation_too_low"

    def test_blocks_kill_switch(self, tmp_path) -> None:
        cfg = RiskConfig()
        exposure = ExposureTracker(cfg)
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        ks.activate()
        gate = RiskGate(config=cfg, exposure=exposure, kill_switch=ks)

        result = gate.check_entry(_snap())
        assert not result.allowed
        assert result.reason == "kill_switch_active"

    def test_blocks_daily_loss(self, tmp_path) -> None:
        cfg = RiskConfig(max_daily_loss_usd=100.0)
        exposure = ExposureTracker(cfg)
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        gate = RiskGate(config=cfg, exposure=exposure, kill_switch=ks)

        # Simulate daily loss
        exposure.record_cycle_open(100.0, 95.0)
        exposure.record_cycle_close(-110.0, 0.1, was_stop=True)

        result = gate.check_entry(_snap())
        assert not result.allowed
        assert result.reason == "daily_loss_exceeded"

    def test_blocks_consecutive_losses(self, tmp_path) -> None:
        cfg = RiskConfig(max_consecutive_losses=3)
        exposure = ExposureTracker(cfg)
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        gate = RiskGate(config=cfg, exposure=exposure, kill_switch=ks)

        for _ in range(3):
            exposure.record_cycle_open(100.0, 95.0)
            exposure.record_cycle_close(-10.0, 0.1, was_stop=True)

        result = gate.check_entry(_snap())
        assert not result.allowed
        assert result.reason == "consecutive_losses_exceeded"

    def test_blocks_during_cooldown(self, tmp_path) -> None:
        cfg = RiskConfig(cooldown_after_stop_bars=10)
        exposure = ExposureTracker(cfg)
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        gate = RiskGate(config=cfg, exposure=exposure, kill_switch=ks)

        exposure.record_cycle_open(100.0, 95.0)
        exposure.record_cycle_close(-10.0, 0.1, was_stop=True)
        # Only 2 bars since stop
        exposure.tick_bar()
        exposure.tick_bar()

        result = gate.check_entry(_snap())
        assert not result.allowed
        assert result.reason == "cooldown_active"

    def test_blocks_tiny_spread_std(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.check_entry(_snap(spread_std=0.00001))
        assert not result.allowed
        assert result.reason == "spread_std_too_small"


class TestRiskGatePosition:
    def test_allows_healthy_position(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        cycle = CycleState(cycle_id="c1", status=CycleStatus.OPEN, unrealized_pnl=-10.0)
        result = gate.check_position(_snap(), cycle)
        assert result.allowed

    def test_stops_on_cycle_loss(self, tmp_path) -> None:
        cfg = RiskConfig(max_cycle_loss_usd=50.0)
        gate = _gate(tmp_path, config=cfg)
        cycle = CycleState(cycle_id="c1", status=CycleStatus.OPEN, unrealized_pnl=-60.0)
        result = gate.check_position(_snap(), cycle)
        assert not result.allowed
        assert result.reason == "cycle_loss_exceeded"


class TestRiskGateAction:
    def test_exit_always_allowed(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        # Even with kill switch, exits should be allowed
        result = gate.check_action(SignalAction.EXIT_FULL, _snap())
        assert result.allowed

    def test_stop_always_allowed(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.check_action(SignalAction.STOP, _snap())
        assert result.allowed

    def test_hold_always_allowed(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.check_action(SignalAction.HOLD, _snap())
        assert result.allowed

    def test_add_level_requires_cycle(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.check_action(SignalAction.ADD_LEVEL, _snap(), cycle=None)
        assert not result.allowed
        assert result.reason == "no_active_cycle"


class TestSystemHealth:
    def test_healthy_system(self, tmp_path) -> None:
        gate = _gate(tmp_path)
        result = gate.is_system_healthy(_snap())
        assert result.allowed

    def test_unhealthy_kill_switch(self, tmp_path) -> None:
        cfg = RiskConfig()
        exposure = ExposureTracker(cfg)
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        ks.activate()
        gate = RiskGate(config=cfg, exposure=exposure, kill_switch=ks)

        result = gate.is_system_healthy(_snap())
        assert not result.allowed
        assert result.reason == "kill_switch_active"

    def test_unhealthy_drawdown(self, tmp_path) -> None:
        cfg = RiskConfig(max_drawdown_usd=100.0)
        exposure = ExposureTracker(cfg)
        exposure.set_peak_equity(1000.0)
        exposure.record_cycle_open(100.0, 95.0)
        exposure.record_cycle_close(-150.0, 0.1, was_stop=True)

        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        gate = RiskGate(config=cfg, exposure=exposure, kill_switch=ks)

        result = gate.is_system_healthy(_snap())
        assert not result.allowed
        assert result.reason == "drawdown_breached"
