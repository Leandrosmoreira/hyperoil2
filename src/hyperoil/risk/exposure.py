"""Exposure tracker — monitors notional exposure, P&L, and drawdown.

Single source of truth for portfolio-level risk metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperoil.config import RiskConfig
from hyperoil.observability.logger import get_logger
from hyperoil.types import now_ms

log = get_logger(__name__)


@dataclass
class DailyStats:
    """P&L and trade stats for the current day."""
    date: str  # YYYY-MM-DD
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_fees: float = 0.0
    cycles_opened: int = 0
    cycles_closed: int = 0
    consecutive_losses: int = 0
    bars_since_last_stop: int = 999


@dataclass
class ExposureSnapshot:
    """Point-in-time exposure metrics."""
    timestamp_ms: int
    total_notional: float
    net_notional_left: float
    net_notional_right: float
    unrealized_pnl: float
    daily_pnl: float
    peak_equity: float
    drawdown_usd: float
    drawdown_pct: float


class ExposureTracker:
    """Tracks portfolio-level exposure, P&L, and drawdown in real time."""

    def __init__(self, config: RiskConfig) -> None:
        self._config = config
        self._daily = DailyStats(date=self._today())
        self._peak_equity: float = 0.0
        self._total_notional: float = 0.0
        self._net_left: float = 0.0
        self._net_right: float = 0.0
        self._unrealized_pnl: float = 0.0

    @property
    def daily_pnl(self) -> float:
        return self._daily.realized_pnl + self._daily.unrealized_pnl

    @property
    def consecutive_losses(self) -> int:
        return self._daily.consecutive_losses

    @property
    def bars_since_last_stop(self) -> int:
        return self._daily.bars_since_last_stop

    @property
    def total_notional(self) -> float:
        return self._total_notional

    @property
    def drawdown_usd(self) -> float:
        equity = self._peak_equity + self.daily_pnl
        if equity >= self._peak_equity:
            return 0.0
        return self._peak_equity - equity

    @property
    def drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return self.drawdown_usd / self._peak_equity

    def record_cycle_open(self, notional_left: float, notional_right: float) -> None:
        """Record exposure from a new cycle opening."""
        self._check_day_rollover()
        self._net_left += notional_left
        self._net_right += notional_right
        self._total_notional = self._net_left + self._net_right
        self._daily.cycles_opened += 1

        log.info(
            "exposure_cycle_opened",
            notional_left=round(notional_left, 2),
            notional_right=round(notional_right, 2),
            total_notional=round(self._total_notional, 2),
        )

    def record_level_add(self, notional_left: float, notional_right: float) -> None:
        """Record additional exposure from a grid level add."""
        self._net_left += notional_left
        self._net_right += notional_right
        self._total_notional = self._net_left + self._net_right

    def record_cycle_close(self, realized_pnl: float, fees: float, was_stop: bool) -> None:
        """Record a cycle close and update daily stats."""
        self._check_day_rollover()
        self._daily.realized_pnl += realized_pnl
        self._daily.total_fees += fees
        self._daily.cycles_closed += 1

        # Reset notional (cycle closed = positions flat)
        self._net_left = 0.0
        self._net_right = 0.0
        self._total_notional = 0.0
        self._unrealized_pnl = 0.0
        self._daily.unrealized_pnl = 0.0

        # Track consecutive losses
        if realized_pnl < 0:
            self._daily.consecutive_losses += 1
        else:
            self._daily.consecutive_losses = 0

        # Track bars since stop
        if was_stop:
            self._daily.bars_since_last_stop = 0

        # Update peak equity
        equity = self._peak_equity + self._daily.realized_pnl
        if equity > self._peak_equity:
            self._peak_equity = equity

        log.info(
            "exposure_cycle_closed",
            realized_pnl=round(realized_pnl, 2),
            daily_pnl=round(self.daily_pnl, 2),
            consecutive_losses=self._daily.consecutive_losses,
        )

    def update_unrealized(self, unrealized_pnl: float) -> None:
        """Update unrealized P&L from the active cycle."""
        self._unrealized_pnl = unrealized_pnl
        self._daily.unrealized_pnl = unrealized_pnl

    def tick_bar(self) -> None:
        """Called each bar — increments bars_since_last_stop."""
        self._daily.bars_since_last_stop += 1

    def get_snapshot(self) -> ExposureSnapshot:
        """Get current exposure metrics."""
        return ExposureSnapshot(
            timestamp_ms=now_ms(),
            total_notional=round(self._total_notional, 2),
            net_notional_left=round(self._net_left, 2),
            net_notional_right=round(self._net_right, 2),
            unrealized_pnl=round(self._unrealized_pnl, 2),
            daily_pnl=round(self.daily_pnl, 2),
            peak_equity=round(self._peak_equity, 2),
            drawdown_usd=round(self.drawdown_usd, 2),
            drawdown_pct=round(self.drawdown_pct, 4),
        )

    def is_daily_loss_breached(self) -> bool:
        return self.daily_pnl <= -self._config.max_daily_loss_usd

    def is_drawdown_breached(self) -> bool:
        return (
            self.drawdown_usd >= self._config.max_drawdown_usd
            or self.drawdown_pct >= self._config.max_drawdown_pct
        )

    def set_peak_equity(self, equity: float) -> None:
        """Set initial peak equity (e.g. from account balance on startup)."""
        self._peak_equity = equity

    def _check_day_rollover(self) -> None:
        """Reset daily stats if the date has changed."""
        today = self._today()
        if today != self._daily.date:
            log.info(
                "daily_rollover",
                old_date=self._daily.date,
                new_date=today,
                realized_pnl=round(self._daily.realized_pnl, 2),
            )
            self._daily = DailyStats(date=today)

    @staticmethod
    def _today() -> str:
        import datetime
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
