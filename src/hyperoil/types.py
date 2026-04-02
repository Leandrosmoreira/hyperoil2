"""Shared types, enums, and dataclasses for HyperOil v2."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto


# --- Enums ---

class Direction(str, Enum):
    LONG_SPREAD = "long_spread"    # long CL, short BRENT
    SHORT_SPREAD = "short_spread"  # short CL, long BRENT


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    SUBSCRIBING = "subscribing"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STALE = "stale"


class Regime(str, Enum):
    GOOD = "good"
    CAUTION = "caution"
    BAD = "bad"
    UNKNOWN = "unknown"


class CycleStatus(str, Enum):
    IDLE = "idle"
    OPENING = "opening"
    OPEN = "open"
    ADDING = "adding"
    REDUCING = "reducing"
    CLOSING = "closing"
    CLOSED = "closed"
    STOPPED = "stopped"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class StopReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS_Z = "stop_loss_z"
    STOP_LOSS_MONETARY = "stop_loss_monetary"
    STOP_TIME = "stop_time"
    CORRELATION_BREAK = "correlation_break"
    REGIME_CHANGE = "regime_change"
    KILL_SWITCH = "kill_switch"
    MAX_MAE = "max_mae"
    MANUAL = "manual"
    END_OF_SESSION = "end_of_session"


class SignalAction(str, Enum):
    ENTER = "enter"
    ADD_LEVEL = "add_level"
    EXIT_PARTIAL = "exit_partial"
    EXIT_FULL = "exit_full"
    STOP = "stop"
    HOLD = "hold"


# --- Dataclasses ---

@dataclass(frozen=True)
class Tick:
    """Single price tick for one symbol."""
    timestamp_ms: int
    symbol: str
    bid: float
    ask: float
    mid: float
    last: float
    volume: float = 0.0


@dataclass(frozen=True)
class SpreadSnapshot:
    """Point-in-time spread computation."""
    timestamp_ms: int
    price_left: float
    price_right: float
    beta: float
    spread: float
    spread_mean: float
    spread_std: float
    zscore: float
    correlation: float
    vol_left: float
    vol_right: float
    regime: Regime


@dataclass
class GridLevel:
    """State of a single grid level within a cycle."""
    level: int
    z_entry: float
    z_current: float
    size_left: float
    size_right: float
    entry_price_left: float
    entry_price_right: float
    entry_beta: float
    entry_timestamp_ms: int
    filled: bool = False
    mae_z: float = 0.0
    mfe_z: float = 0.0
    bars_held: int = 0
    realized_pnl: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0


@dataclass
class CycleState:
    """Full state of an active trading cycle."""
    cycle_id: str
    status: CycleStatus = CycleStatus.IDLE
    direction: Direction | None = None
    opened_at_ms: int = 0
    last_action_ms: int = 0
    levels: list[GridLevel] = field(default_factory=list)
    max_level_filled: int = 0
    entry_z_avg: float = 0.0
    current_z: float = 0.0
    peak_adverse_z: float = 0.0
    peak_favorable_z: float = 0.0
    total_size_left: float = 0.0
    total_size_right: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    stop_reason: StopReason | None = None
    closed_at_ms: int = 0


@dataclass(frozen=True)
class OrderRequest:
    """Request to send an order to the exchange."""
    order_id: str
    cycle_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float | None  # None = market order
    level: int
    leg: str  # "left" or "right"


@dataclass
class OrderState:
    """Tracked state of an order through its lifecycle."""
    order_id: str
    cycle_id: str
    symbol: str
    side: OrderSide
    qty_requested: float
    qty_filled: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    exchange_order_id: str | None = None
    created_at_ms: int = 0
    updated_at_ms: int = 0
    fees: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class RiskCheckResult:
    """Result of a pre-trade risk check."""
    allowed: bool
    reason: str
    details: dict[str, float | str | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthStatus:
    """System health snapshot."""
    timestamp_ms: int
    ws_state: ConnectionState
    last_tick_ms: int
    position_open: bool
    cycle_status: CycleStatus
    current_z: float
    regime: Regime
    daily_pnl: float
    kill_switch_active: bool
    uptime_sec: float


def now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)
