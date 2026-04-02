"""Global application state with persistence support."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from hyperoil.observability.logger import get_logger
from hyperoil.types import (
    ConnectionState,
    CycleState,
    CycleStatus,
    HealthStatus,
    Regime,
    now_ms,
)

log = get_logger(__name__)


@dataclass
class AppState:
    """Global mutable state for the application."""

    # Connection
    ws_state: ConnectionState = ConnectionState.DISCONNECTED
    last_tick_ms: int = 0

    # Trading
    active_cycle: CycleState | None = None
    completed_cycles: list[str] = field(default_factory=list)  # cycle_ids

    # Risk
    daily_pnl: float = 0.0
    daily_trades: int = 0
    consecutive_losses: int = 0
    kill_switch_active: bool = False
    current_regime: Regime = Regime.UNKNOWN

    # Signal
    current_z: float = 0.0
    current_spread: float = 0.0
    current_correlation: float = 0.0

    # System
    started_at: float = field(default_factory=time.time)

    def to_health(self) -> HealthStatus:
        return HealthStatus(
            timestamp_ms=now_ms(),
            ws_state=self.ws_state,
            last_tick_ms=self.last_tick_ms,
            position_open=self.active_cycle is not None
            and self.active_cycle.status == CycleStatus.OPEN,
            cycle_status=self.active_cycle.status if self.active_cycle else CycleStatus.IDLE,
            current_z=self.current_z,
            regime=self.current_regime,
            daily_pnl=self.daily_pnl,
            kill_switch_active=self.kill_switch_active,
            uptime_sec=time.time() - self.started_at,
        )

    def save_snapshot(self, path: str) -> None:
        """Save state snapshot to JSON file for crash recovery."""
        snapshot = {
            "timestamp_ms": now_ms(),
            "ws_state": self.ws_state.value,
            "last_tick_ms": self.last_tick_ms,
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "consecutive_losses": self.consecutive_losses,
            "kill_switch_active": self.kill_switch_active,
            "current_regime": self.current_regime.value,
            "current_z": self.current_z,
            "current_spread": self.current_spread,
            "current_correlation": self.current_correlation,
            "has_active_cycle": self.active_cycle is not None,
        }

        if self.active_cycle:
            snapshot["active_cycle"] = {
                "cycle_id": self.active_cycle.cycle_id,
                "status": self.active_cycle.status.value,
                "direction": self.active_cycle.direction.value if self.active_cycle.direction else None,
                "max_level_filled": self.active_cycle.max_level_filled,
                "realized_pnl": self.active_cycle.realized_pnl,
                "unrealized_pnl": self.active_cycle.unrealized_pnl,
            }

        filepath = Path(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(json.dumps(snapshot, indent=2))
        log.debug("state_snapshot_saved", path=path)

    def load_snapshot(self, path: str) -> bool:
        """Load state from snapshot. Returns True if loaded successfully."""
        filepath = Path(path)
        if not filepath.exists():
            return False

        try:
            snapshot = json.loads(filepath.read_text())
            self.daily_pnl = snapshot.get("daily_pnl", 0.0)
            self.daily_trades = snapshot.get("daily_trades", 0)
            self.consecutive_losses = snapshot.get("consecutive_losses", 0)
            self.kill_switch_active = snapshot.get("kill_switch_active", False)
            self.current_regime = Regime(snapshot.get("current_regime", "unknown"))
            self.current_z = snapshot.get("current_z", 0.0)
            log.info("state_snapshot_loaded", path=path, daily_pnl=self.daily_pnl)
            return True
        except Exception:
            log.exception("state_snapshot_load_failed", path=path)
            return False
