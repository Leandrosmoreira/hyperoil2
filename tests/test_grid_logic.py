"""Tests for grid decision engine — entry, exit, add, stop logic."""

from __future__ import annotations

from hyperoil.config import GridConfig, GridLevelConfig, RiskConfig
from hyperoil.strategy.grid_pairs import GridDecisionEngine
from hyperoil.types import (
    CycleState,
    CycleStatus,
    Direction,
    GridLevel,
    Regime,
    SignalAction,
    SpreadSnapshot,
    StopReason,
    now_ms,
)


def _make_snapshot(
    zscore: float = 0.0,
    regime: Regime = Regime.GOOD,
    correlation: float = 0.85,
    beta: float = 0.95,
    spread_std: float = 0.002,
) -> SpreadSnapshot:
    return SpreadSnapshot(
        timestamp_ms=now_ms(),
        price_left=68.50,
        price_right=72.30,
        beta=beta,
        spread=-0.004,
        spread_mean=-0.002,
        spread_std=spread_std,
        zscore=zscore,
        correlation=correlation,
        vol_left=0.25,
        vol_right=0.23,
        regime=regime,
    )


def _make_open_cycle(
    direction: Direction = Direction.LONG_SPREAD,
    level: int = 1,
    z_entry: float = -1.6,
    unrealized_pnl: float = 0.0,
) -> CycleState:
    ts = now_ms()
    return CycleState(
        cycle_id="test-cycle-001",
        status=CycleStatus.OPEN,
        direction=direction,
        opened_at_ms=ts,
        last_action_ms=ts,
        levels=[
            GridLevel(
                level=level,
                z_entry=z_entry,
                z_current=z_entry,
                size_left=1.46,
                size_right=1.38,
                entry_price_left=68.50,
                entry_price_right=72.30,
                entry_beta=0.95,
                entry_timestamp_ms=ts,
                filled=True,
            ),
        ],
        max_level_filled=level,
        entry_z_avg=z_entry,
        current_z=z_entry,
        total_size_left=1.46,
        total_size_right=1.38,
        unrealized_pnl=unrealized_pnl,
    )


def _default_grid() -> GridConfig:
    return GridConfig(
        entry_z=1.5,
        exit_z=0.2,
        stop_z=4.5,
        cooldown_bars=3,
        max_levels=4,
        anti_repeat_bars=12,
        levels=[
            GridLevelConfig(z=1.5, mult=1.0),
            GridLevelConfig(z=2.0, mult=1.2),
            GridLevelConfig(z=2.5, mult=1.5),
            GridLevelConfig(z=3.0, mult=2.0),
        ],
    )


def _default_risk() -> RiskConfig:
    return RiskConfig()


class TestEntryDecisions:
    def test_no_entry_below_threshold(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, _ = engine.evaluate(
            _make_snapshot(zscore=1.0), None, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.HOLD

    def test_entry_long_spread_negative_z(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, details = engine.evaluate(
            _make_snapshot(zscore=-1.6), None, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.ENTER
        assert details["direction"] == Direction.LONG_SPREAD
        assert details["level"] == 1

    def test_entry_short_spread_positive_z(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, details = engine.evaluate(
            _make_snapshot(zscore=1.8), None, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.ENTER
        assert details["direction"] == Direction.SHORT_SPREAD

    def test_no_entry_bad_regime(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-2.0, regime=Regime.BAD), None,
            bars_since_last_stop=100, consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.HOLD

    def test_no_entry_low_correlation(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-2.0, correlation=0.45), None,
            bars_since_last_stop=100, consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.HOLD

    def test_no_entry_daily_loss_exceeded(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-2.0), None, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=-350.0, kill_switch=False,
        )
        assert action == SignalAction.HOLD

    def test_no_entry_cooldown_active(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-2.0), None, bars_since_last_stop=5,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.HOLD

    def test_no_entry_consecutive_losses(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-2.0), None, bars_since_last_stop=100,
            consecutive_losses=6, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.HOLD


class TestExitDecisions:
    def test_take_profit_long_spread(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(Direction.LONG_SPREAD, z_entry=-1.6)
        action, details = engine.evaluate(
            _make_snapshot(zscore=-0.1), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.EXIT_FULL
        assert details["reason"] == StopReason.TAKE_PROFIT

    def test_take_profit_short_spread(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(Direction.SHORT_SPREAD, z_entry=1.8)
        action, details = engine.evaluate(
            _make_snapshot(zscore=0.1), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.EXIT_FULL
        assert details["reason"] == StopReason.TAKE_PROFIT

    def test_hold_when_not_at_exit(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(Direction.LONG_SPREAD, z_entry=-1.6)
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-1.0), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.HOLD


class TestStopDecisions:
    def test_stop_z_extreme(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(Direction.LONG_SPREAD, z_entry=-1.6)
        action, details = engine.evaluate(
            _make_snapshot(zscore=-4.6), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.STOP
        assert details["reason"] == StopReason.STOP_LOSS_Z

    def test_stop_monetary_loss(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(unrealized_pnl=-130.0)  # > max_cycle_loss_usd=120
        action, details = engine.evaluate(
            _make_snapshot(zscore=-2.0), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.STOP
        assert details["reason"] == StopReason.STOP_LOSS_MONETARY

    def test_stop_correlation_break(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle()
        action, details = engine.evaluate(
            _make_snapshot(zscore=-1.0, correlation=0.50), cycle,
            bars_since_last_stop=100, consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.STOP
        assert details["reason"] == StopReason.CORRELATION_BREAK

    def test_stop_regime_bad(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle()
        action, details = engine.evaluate(
            _make_snapshot(zscore=-1.0, regime=Regime.BAD), cycle,
            bars_since_last_stop=100, consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.STOP
        assert details["reason"] == StopReason.REGIME_CHANGE


class TestAddLevelDecisions:
    def test_add_level_when_z_deepens(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(Direction.LONG_SPREAD, level=1, z_entry=-1.6)
        action, details = engine.evaluate(
            _make_snapshot(zscore=-2.1), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action == SignalAction.ADD_LEVEL
        assert details["level"] == 2

    def test_no_add_when_max_levels(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(Direction.LONG_SPREAD, level=1, z_entry=-1.6)
        cycle.max_level_filled = 4  # already at max
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-3.5), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        # Should either hold or stop, not add
        assert action != SignalAction.ADD_LEVEL

    def test_no_add_during_cooldown(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle(Direction.LONG_SPREAD, level=1, z_entry=-1.6)
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-2.1), cycle, bars_since_last_stop=1,  # cooldown
            consecutive_losses=0, daily_pnl=0, kill_switch=False,
        )
        assert action != SignalAction.ADD_LEVEL


class TestKillSwitch:
    def test_kill_switch_closes_open_cycle(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        cycle = _make_open_cycle()
        action, details = engine.evaluate(
            _make_snapshot(zscore=-1.0), cycle, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=True,
        )
        assert action == SignalAction.EXIT_FULL
        assert details["reason"] == StopReason.KILL_SWITCH

    def test_kill_switch_blocks_entry(self) -> None:
        engine = GridDecisionEngine(_default_grid(), _default_risk())
        action, _ = engine.evaluate(
            _make_snapshot(zscore=-2.0), None, bars_since_last_stop=100,
            consecutive_losses=0, daily_pnl=0, kill_switch=True,
        )
        assert action == SignalAction.HOLD
