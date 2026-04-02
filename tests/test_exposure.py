"""Tests for exposure tracker."""

from __future__ import annotations

from hyperoil.config import RiskConfig
from hyperoil.risk.exposure import ExposureTracker


def _tracker() -> ExposureTracker:
    return ExposureTracker(RiskConfig())


class TestExposureTracker:
    def test_initial_state(self) -> None:
        t = _tracker()
        assert t.daily_pnl == 0.0
        assert t.total_notional == 0.0
        assert t.consecutive_losses == 0
        assert t.bars_since_last_stop == 999

    def test_record_cycle_open(self) -> None:
        t = _tracker()
        t.record_cycle_open(notional_left=100.0, notional_right=95.0)
        assert t.total_notional == 195.0

    def test_record_level_add(self) -> None:
        t = _tracker()
        t.record_cycle_open(100.0, 95.0)
        t.record_level_add(120.0, 114.0)
        assert t.total_notional == 429.0

    def test_record_cycle_close_profit(self) -> None:
        t = _tracker()
        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(realized_pnl=25.0, fees=0.5, was_stop=False)

        assert t.daily_pnl == 25.0
        assert t.total_notional == 0.0
        assert t.consecutive_losses == 0

    def test_record_cycle_close_loss(self) -> None:
        t = _tracker()
        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(realized_pnl=-30.0, fees=0.5, was_stop=True)

        assert t.daily_pnl == -30.0
        assert t.consecutive_losses == 1
        assert t.bars_since_last_stop == 0

    def test_consecutive_losses_reset_on_profit(self) -> None:
        t = _tracker()

        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(-10.0, 0.1, was_stop=True)
        assert t.consecutive_losses == 1

        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(-15.0, 0.1, was_stop=True)
        assert t.consecutive_losses == 2

        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(20.0, 0.1, was_stop=False)
        assert t.consecutive_losses == 0

    def test_update_unrealized(self) -> None:
        t = _tracker()
        t.record_cycle_open(100.0, 95.0)
        t.update_unrealized(15.0)
        assert t.daily_pnl == 15.0  # realized(0) + unrealized(15)

    def test_tick_bar(self) -> None:
        t = _tracker()
        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(-10.0, 0.1, was_stop=True)
        assert t.bars_since_last_stop == 0

        t.tick_bar()
        t.tick_bar()
        assert t.bars_since_last_stop == 2

    def test_is_daily_loss_breached(self) -> None:
        t = ExposureTracker(RiskConfig(max_daily_loss_usd=100.0))
        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(-110.0, 0.1, was_stop=True)
        assert t.is_daily_loss_breached()

    def test_is_daily_loss_not_breached(self) -> None:
        t = ExposureTracker(RiskConfig(max_daily_loss_usd=300.0))
        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(-50.0, 0.1, was_stop=True)
        assert not t.is_daily_loss_breached()

    def test_drawdown_tracking(self) -> None:
        t = _tracker()
        t.set_peak_equity(1000.0)
        t.record_cycle_open(100.0, 95.0)
        t.record_cycle_close(-200.0, 0.1, was_stop=True)

        assert t.drawdown_usd == 200.0
        assert abs(t.drawdown_pct - 0.20) < 0.01

    def test_snapshot(self) -> None:
        t = _tracker()
        t.record_cycle_open(100.0, 95.0)
        t.update_unrealized(10.0)

        snap = t.get_snapshot()
        assert snap.total_notional == 195.0
        assert snap.unrealized_pnl == 10.0
        assert snap.daily_pnl == 10.0
        assert snap.timestamp_ms > 0
