"""Tests for the Rich terminal dashboard."""

from __future__ import annotations

from hyperoil.observability.dashboard import (
    DashboardData,
    DashboardManager,
    build_dashboard,
    build_pnl_panel,
    build_position_panel,
    build_risk_panel,
    build_signal_panel,
    build_system_panel,
)
from hyperoil.types import (
    ConnectionState,
    CycleState,
    CycleStatus,
    Direction,
    Regime,
)


def _data(**kwargs) -> DashboardData:
    return DashboardData(**kwargs)


class TestDashboardPanels:
    def test_signal_panel_renders(self) -> None:
        data = _data(current_z=-1.8, regime=Regime.GOOD, current_beta=0.95)
        panel = build_signal_panel(data)
        assert panel.title == "Signal"

    def test_signal_panel_extreme_z(self) -> None:
        """Extreme z should still render without error."""
        data = _data(current_z=-4.5, regime=Regime.BAD)
        panel = build_signal_panel(data)
        assert panel is not None

    def test_position_panel_idle(self) -> None:
        data = _data()
        panel = build_position_panel(data)
        assert panel.title == "Position"

    def test_position_panel_open_cycle(self) -> None:
        cycle = CycleState(
            cycle_id="c1",
            status=CycleStatus.OPEN,
            direction=Direction.LONG_SPREAD,
            max_level_filled=2,
            entry_z_avg=-1.8,
            current_z=-1.2,
            total_size_left=1.46,
            total_size_right=1.38,
            unrealized_pnl=12.50,
        )
        data = _data(cycle=cycle)
        panel = build_position_panel(data)
        assert panel is not None

    def test_pnl_panel_positive(self) -> None:
        data = _data(daily_pnl=150.0, cumulative_pnl=500.0)
        panel = build_pnl_panel(data)
        assert panel.title == "P&L"

    def test_pnl_panel_negative(self) -> None:
        data = _data(daily_pnl=-80.0, cumulative_pnl=-200.0)
        panel = build_pnl_panel(data)
        assert panel is not None

    def test_risk_panel_normal(self) -> None:
        data = _data(kill_switch_active=False, consecutive_losses=2)
        panel = build_risk_panel(data)
        assert panel.title == "Risk"

    def test_risk_panel_kill_switch(self) -> None:
        data = _data(kill_switch_active=True)
        panel = build_risk_panel(data)
        assert panel is not None

    def test_system_panel(self) -> None:
        data = _data(
            ws_state=ConnectionState.CONNECTED,
            mode="paper",
            uptime_sec=3661.0,
        )
        panel = build_system_panel(data)
        assert panel.title == "System"


class TestDashboardLayout:
    def test_full_dashboard_renders(self) -> None:
        data = _data(
            current_z=-1.6,
            regime=Regime.GOOD,
            ws_state=ConnectionState.CONNECTED,
            daily_pnl=50.0,
        )
        layout = build_dashboard(data)
        assert layout is not None

    def test_dashboard_with_all_states(self) -> None:
        """Dashboard should handle all possible states without crashing."""
        for regime in Regime:
            for ws in ConnectionState:
                data = _data(regime=regime, ws_state=ws)
                layout = build_dashboard(data)
                assert layout is not None


class TestDashboardManager:
    def test_create_manager(self) -> None:
        mgr = DashboardManager(refresh_ms=500)
        assert mgr.data is not None

    def test_update_data(self) -> None:
        mgr = DashboardManager()
        mgr.update(current_z=-2.0, daily_pnl=100.0, mode="live")
        assert mgr.data.current_z == -2.0
        assert mgr.data.daily_pnl == 100.0
        assert mgr.data.mode == "live"

    def test_update_ignores_unknown_fields(self) -> None:
        mgr = DashboardManager()
        mgr.update(nonexistent_field=42)
        # Should not raise

    def test_render(self) -> None:
        mgr = DashboardManager()
        mgr.update(current_z=-1.5)
        layout = mgr.render()
        assert layout is not None
