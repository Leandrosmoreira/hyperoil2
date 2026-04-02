"""Tests for risk rules."""

from __future__ import annotations

from hyperoil.config import RiskConfig
from hyperoil.risk.rules import (
    RiskContext,
    check_consecutive_losses,
    check_cooldown,
    check_correlation,
    check_cycle_loss,
    check_daily_loss,
    check_kill_switch,
    check_regime,
    check_spread_validity,
)
from hyperoil.types import (
    CycleState,
    CycleStatus,
    Regime,
    SpreadSnapshot,
    now_ms,
)


def _snap(
    regime: Regime = Regime.GOOD,
    correlation: float = 0.85,
    spread_std: float = 0.002,
) -> SpreadSnapshot:
    return SpreadSnapshot(
        timestamp_ms=now_ms(),
        price_left=68.50,
        price_right=72.30,
        beta=0.95,
        spread=-0.004,
        spread_mean=-0.002,
        spread_std=spread_std,
        zscore=-1.6,
        correlation=correlation,
        vol_left=0.25,
        vol_right=0.23,
        regime=regime,
    )


def _ctx(
    regime: Regime = Regime.GOOD,
    correlation: float = 0.85,
    daily_pnl: float = 0.0,
    consecutive_losses: int = 0,
    bars_since_last_stop: int = 100,
    kill_switch: bool = False,
    spread_std: float = 0.002,
    cycle: CycleState | None = None,
) -> RiskContext:
    return RiskContext(
        snapshot=_snap(regime=regime, correlation=correlation, spread_std=spread_std),
        cycle=cycle,
        daily_pnl=daily_pnl,
        consecutive_losses=consecutive_losses,
        bars_since_last_stop=bars_since_last_stop,
        total_notional=0.0,
        kill_switch_active=kill_switch,
    )


def _cfg() -> RiskConfig:
    return RiskConfig()


class TestKillSwitchRule:
    def test_blocks_when_active(self) -> None:
        result = check_kill_switch(_ctx(kill_switch=True), _cfg())
        assert not result.allowed
        assert result.reason == "kill_switch_active"

    def test_allows_when_inactive(self) -> None:
        result = check_kill_switch(_ctx(kill_switch=False), _cfg())
        assert result.allowed


class TestRegimeRule:
    def test_blocks_bad_regime(self) -> None:
        result = check_regime(_ctx(regime=Regime.BAD), _cfg())
        assert not result.allowed
        assert result.reason == "regime_bad"

    def test_allows_good_regime(self) -> None:
        result = check_regime(_ctx(regime=Regime.GOOD), _cfg())
        assert result.allowed

    def test_allows_caution_regime(self) -> None:
        result = check_regime(_ctx(regime=Regime.CAUTION), _cfg())
        assert result.allowed

    def test_allows_bad_when_disabled(self) -> None:
        cfg = RiskConfig(pause_on_bad_regime=False)
        result = check_regime(_ctx(regime=Regime.BAD), cfg)
        assert result.allowed


class TestCorrelationRule:
    def test_blocks_low_correlation(self) -> None:
        result = check_correlation(_ctx(correlation=0.45), _cfg())
        assert not result.allowed
        assert result.reason == "correlation_too_low"

    def test_allows_high_correlation(self) -> None:
        result = check_correlation(_ctx(correlation=0.85), _cfg())
        assert result.allowed

    def test_blocks_at_boundary(self) -> None:
        cfg = RiskConfig(min_correlation=0.60)
        result = check_correlation(_ctx(correlation=0.59), cfg)
        assert not result.allowed


class TestDailyLossRule:
    def test_blocks_when_exceeded(self) -> None:
        result = check_daily_loss(_ctx(daily_pnl=-350.0), _cfg())
        assert not result.allowed

    def test_allows_within_limit(self) -> None:
        result = check_daily_loss(_ctx(daily_pnl=-100.0), _cfg())
        assert result.allowed

    def test_blocks_at_exact_limit(self) -> None:
        cfg = RiskConfig(max_daily_loss_usd=300.0)
        result = check_daily_loss(_ctx(daily_pnl=-300.0), cfg)
        assert not result.allowed


class TestConsecutiveLossesRule:
    def test_blocks_too_many(self) -> None:
        result = check_consecutive_losses(_ctx(consecutive_losses=6), _cfg())
        assert not result.allowed

    def test_allows_within_limit(self) -> None:
        result = check_consecutive_losses(_ctx(consecutive_losses=2), _cfg())
        assert result.allowed


class TestCooldownRule:
    def test_blocks_during_cooldown(self) -> None:
        result = check_cooldown(_ctx(bars_since_last_stop=5), _cfg())
        assert not result.allowed

    def test_allows_after_cooldown(self) -> None:
        result = check_cooldown(_ctx(bars_since_last_stop=100), _cfg())
        assert result.allowed


class TestSpreadValidityRule:
    def test_blocks_tiny_std(self) -> None:
        result = check_spread_validity(_ctx(spread_std=0.00001), _cfg())
        assert not result.allowed

    def test_allows_normal_std(self) -> None:
        result = check_spread_validity(_ctx(spread_std=0.002), _cfg())
        assert result.allowed


class TestCycleLossRule:
    def test_blocks_exceeded_loss(self) -> None:
        cycle = CycleState(
            cycle_id="c1", status=CycleStatus.OPEN,
            unrealized_pnl=-130.0,
        )
        result = check_cycle_loss(_ctx(cycle=cycle), _cfg())
        assert not result.allowed
        assert result.reason == "cycle_loss_exceeded"

    def test_allows_within_limit(self) -> None:
        cycle = CycleState(
            cycle_id="c1", status=CycleStatus.OPEN,
            unrealized_pnl=-50.0,
        )
        result = check_cycle_loss(_ctx(cycle=cycle), _cfg())
        assert result.allowed

    def test_allows_no_cycle(self) -> None:
        result = check_cycle_loss(_ctx(), _cfg())
        assert result.allowed
