"""Rich terminal dashboard — live trading display for operators.

Displays spread, z-score, positions, P&L, risk metrics, and system health
in a live-updating terminal UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hyperoil.types import (
    ConnectionState,
    CycleState,
    CycleStatus,
    Regime,
)


@dataclass
class DashboardData:
    """All data needed to render the dashboard."""
    # Connection
    ws_state: ConnectionState = ConnectionState.DISCONNECTED
    last_tick_ms: int = 0
    # Signal
    current_z: float = 0.0
    current_spread: float = 0.0
    current_beta: float = 0.0
    current_correlation: float = 0.0
    regime: Regime = Regime.UNKNOWN
    price_left: float = 0.0
    price_right: float = 0.0
    # Position
    cycle: CycleState | None = None
    # P&L
    daily_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_fees: float = 0.0
    cumulative_pnl: float = 0.0
    # Risk
    consecutive_losses: int = 0
    bars_since_last_stop: int = 0
    kill_switch_active: bool = False
    total_notional: float = 0.0
    drawdown_usd: float = 0.0
    # System
    uptime_sec: float = 0.0
    mode: str = "paper"
    bars_processed: int = 0


def _regime_color(regime: Regime) -> str:
    return {"good": "green", "caution": "yellow", "bad": "red"}.get(regime.value, "white")


def _ws_color(state: ConnectionState) -> str:
    if state == ConnectionState.CONNECTED:
        return "green"
    if state in (ConnectionState.CONNECTING, ConnectionState.SUBSCRIBING, ConnectionState.RECONNECTING):
        return "yellow"
    return "red"


def _pnl_color(pnl: float) -> str:
    if pnl > 0:
        return "green"
    if pnl < 0:
        return "red"
    return "white"


def build_signal_panel(data: DashboardData) -> Panel:
    """Build the signal/spread panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("key", style="bold", width=16)
    table.add_column("value", width=20)

    z_color = "red" if abs(data.current_z) > 2.5 else "yellow" if abs(data.current_z) > 1.5 else "white"

    table.add_row("Z-Score", f"[{z_color}]{data.current_z:+.4f}[/]")
    table.add_row("Spread", f"{data.current_spread:.6f}")
    table.add_row("Beta", f"{data.current_beta:.4f}")
    table.add_row("Correlation", f"{data.current_correlation:.4f}")
    table.add_row("Regime", f"[{_regime_color(data.regime)}]{data.regime.value.upper()}[/]")
    table.add_row("Price Left", f"${data.price_left:.2f}")
    table.add_row("Price Right", f"${data.price_right:.2f}")

    return Panel(table, title="Signal", border_style="blue")


def build_position_panel(data: DashboardData) -> Panel:
    """Build the position/cycle panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("key", style="bold", width=16)
    table.add_column("value", width=20)

    cycle = data.cycle
    if cycle and cycle.status in (CycleStatus.OPEN, CycleStatus.ADDING):
        direction = cycle.direction.value if cycle.direction else "none"
        table.add_row("Status", f"[green]{cycle.status.value.upper()}[/]")
        table.add_row("Direction", direction)
        table.add_row("Levels", f"{cycle.max_level_filled}")
        table.add_row("Entry Z Avg", f"{cycle.entry_z_avg:+.4f}")
        table.add_row("Current Z", f"{cycle.current_z:+.4f}")
        table.add_row("Size Left", f"{cycle.total_size_left:.4f}")
        table.add_row("Size Right", f"{cycle.total_size_right:.4f}")

        pnl_color = _pnl_color(cycle.unrealized_pnl)
        table.add_row("Unrealized", f"[{pnl_color}]${cycle.unrealized_pnl:+.2f}[/]")
    else:
        table.add_row("Status", "[dim]IDLE[/]")
        table.add_row("Direction", "-")
        table.add_row("Levels", "-")
        table.add_row("Entry Z Avg", "-")
        table.add_row("Current Z", "-")
        table.add_row("Size Left", "-")
        table.add_row("Size Right", "-")
        table.add_row("Unrealized", "-")

    return Panel(table, title="Position", border_style="cyan")


def build_pnl_panel(data: DashboardData) -> Panel:
    """Build the P&L panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("key", style="bold", width=16)
    table.add_column("value", width=20)

    table.add_row("Daily P&L", f"[{_pnl_color(data.daily_pnl)}]${data.daily_pnl:+.2f}[/]")
    table.add_row("Unrealized", f"[{_pnl_color(data.unrealized_pnl)}]${data.unrealized_pnl:+.2f}[/]")
    table.add_row("Cumulative", f"[{_pnl_color(data.cumulative_pnl)}]${data.cumulative_pnl:+.2f}[/]")
    table.add_row("Total Fees", f"${data.total_fees:.2f}")
    table.add_row("Notional", f"${data.total_notional:.2f}")

    return Panel(table, title="P&L", border_style="green")


def build_risk_panel(data: DashboardData) -> Panel:
    """Build the risk panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("key", style="bold", width=16)
    table.add_column("value", width=20)

    ks_text = "[red bold]ACTIVE[/]" if data.kill_switch_active else "[green]OFF[/]"
    table.add_row("Kill Switch", ks_text)
    table.add_row("Consec. Losses", str(data.consecutive_losses))
    table.add_row("Bars Since Stop", str(data.bars_since_last_stop))
    table.add_row("Drawdown", f"[{_pnl_color(-data.drawdown_usd)}]${data.drawdown_usd:.2f}[/]")

    return Panel(table, title="Risk", border_style="red")


def build_system_panel(data: DashboardData) -> Panel:
    """Build the system health panel."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("key", style="bold", width=16)
    table.add_column("value", width=20)

    ws_color = _ws_color(data.ws_state)
    table.add_row("WS State", f"[{ws_color}]{data.ws_state.value.upper()}[/]")
    table.add_row("Mode", data.mode.upper())
    table.add_row("Bars Processed", str(data.bars_processed))

    hours = int(data.uptime_sec // 3600)
    mins = int((data.uptime_sec % 3600) // 60)
    secs = int(data.uptime_sec % 60)
    table.add_row("Uptime", f"{hours:02d}:{mins:02d}:{secs:02d}")

    return Panel(table, title="System", border_style="magenta")


def build_dashboard(data: DashboardData) -> Layout:
    """Build the complete dashboard layout."""
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )

    # Header
    header_text = Text(" HyperOil v2 — Pair Trading Dashboard ", style="bold white on blue", justify="center")
    layout["header"].update(Panel(header_text, style="blue"))

    # Body: 2 rows of panels
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )

    layout["left"].split_column(
        Layout(build_signal_panel(data), name="signal"),
        Layout(build_position_panel(data), name="position"),
    )

    layout["right"].split_column(
        Layout(build_pnl_panel(data), name="pnl"),
        Layout(build_risk_panel(data), name="risk"),
        Layout(build_system_panel(data), name="system"),
    )

    # Footer
    footer_text = Text(
        " [Q] Quit  |  [K] Kill Switch  |  Ctrl+C Graceful Shutdown ",
        style="dim",
        justify="center",
    )
    layout["footer"].update(Panel(footer_text, style="dim"))

    return layout


class DashboardManager:
    """Manages the Rich Live display for the terminal dashboard."""

    def __init__(self, refresh_ms: int = 500) -> None:
        self._console = Console()
        self._refresh_rate = refresh_ms / 1000.0
        self._data = DashboardData()
        self._live: Live | None = None

    @property
    def data(self) -> DashboardData:
        return self._data

    def update(self, **kwargs: Any) -> None:
        """Update dashboard data fields."""
        for key, value in kwargs.items():
            if hasattr(self._data, key):
                setattr(self._data, key, value)

    def render(self) -> Layout:
        """Render the current dashboard state."""
        return build_dashboard(self._data)

    def start(self) -> Live:
        """Start the live display. Returns the Live context for the caller to manage."""
        self._live = Live(
            self.render(),
            console=self._console,
            refresh_per_second=1 / self._refresh_rate,
            screen=True,
        )
        return self._live

    def refresh(self) -> None:
        """Refresh the live display with current data."""
        if self._live:
            self._live.update(self.render())
