"""Backtest simulator — runs strategy over historical data with realistic fills.

Integrates replay engine, signal engine, grid decision engine, cycle manager,
and fill tracker into a single deterministic simulation loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperoil.backtest.replay_engine import PairBar, ReplayEngine
from hyperoil.config import AppConfig, BacktestConfig
from hyperoil.execution.fill_tracker import Fill, FillTracker
from hyperoil.observability.logger import get_logger
from hyperoil.signals.signal_engine import SignalEngine
from hyperoil.strategy.grid_pairs import GridDecisionEngine
from hyperoil.strategy.lifecycle import CycleManager
from hyperoil.types import (
    CycleState,
    Direction,
    OrderSide,
    SignalAction,
    SpreadSnapshot,
    StopReason,
)

log = get_logger(__name__)


@dataclass
class TradeRecord:
    """Record of a completed trade cycle for analysis."""
    cycle_id: str
    direction: str
    levels_used: int
    entry_z_avg: float
    exit_z: float
    bars_held: int
    gross_pnl: float
    fees: float
    net_pnl: float
    entry_timestamp_ms: int
    exit_timestamp_ms: int
    stop_reason: str


@dataclass
class SimulationResult:
    """Full result of a backtest simulation."""
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    total_bars: int = 0
    signals_generated: int = 0


class Simulator:
    """Runs a complete backtest simulation.

    Deterministic: same data + same config = same result.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._signal_engine = SignalEngine(config.signal)
        self._decision_engine = GridDecisionEngine(config.grid, config.risk)
        self._cycle_mgr = CycleManager(config.sizing, config.grid)
        self._fill_tracker = FillTracker(config.backtest)

        self._consecutive_losses: int = 0
        self._bars_since_last_stop: int = 999
        self._daily_pnl: float = 0.0
        self._cumulative_pnl: float = 0.0
        self._kill_switch: bool = False

    def run(self, replay: ReplayEngine) -> SimulationResult:
        """Run the simulation over all bars in the replay engine."""
        result = SimulationResult()
        replay.reset()

        # Phase 1: Load history into signal engine to warm up indicators
        bars = replay.iter_bars()
        if not bars:
            return result

        warmup_needed = max(
            self._config.signal.z_window,
            self._config.signal.beta_window,
            self._config.signal.correlation_window,
            self._config.signal.volatility_window,
        ) + 50  # buffer

        warmup_count = min(warmup_needed, len(bars))

        for bar in bars[:warmup_count]:
            self._feed_bar(bar)

        # Phase 2: Run strategy on remaining bars
        for bar in bars[warmup_count:]:
            self._feed_bar(bar)
            result.total_bars += 1

            snapshot = self._signal_engine.compute()
            if snapshot is None:
                continue

            result.signals_generated += 1
            self._bars_since_last_stop += 1

            trade = self._process_bar(snapshot, bar)
            if trade:
                result.trades.append(trade)

            result.equity_curve.append(self._cumulative_pnl)

        # Close any remaining open cycle
        if self._cycle_mgr.has_open_cycle:
            trade = self._force_close_cycle(bars[-1] if bars else None)
            if trade:
                result.trades.append(trade)

        log.info(
            "simulation_complete",
            total_bars=result.total_bars,
            trades=len(result.trades),
            net_pnl=round(self._cumulative_pnl, 2),
        )

        return result

    def _feed_bar(self, bar: PairBar) -> None:
        """Feed a bar into the signal engine."""
        self._signal_engine.add_candle(
            symbol=self._config.symbols.left,
            timestamp_ms=bar.timestamp_ms,
            open=bar.left.open,
            high=bar.left.high,
            low=bar.left.low,
            close=bar.left.close,
            volume=bar.left.volume,
        )
        self._signal_engine.add_candle(
            symbol=self._config.symbols.right,
            timestamp_ms=bar.timestamp_ms,
            open=bar.right.open,
            high=bar.right.high,
            low=bar.right.low,
            close=bar.right.close,
            volume=bar.right.volume,
        )

    def _process_bar(self, snapshot: SpreadSnapshot, bar: PairBar) -> TradeRecord | None:
        """Process a single bar through the strategy pipeline."""
        cycle = self._cycle_mgr.active_cycle

        # Update active cycle with new prices
        if cycle:
            self._cycle_mgr.update(snapshot)

        action, details = self._decision_engine.evaluate(
            snapshot=snapshot,
            cycle=cycle,
            bars_since_last_stop=self._bars_since_last_stop,
            consecutive_losses=self._consecutive_losses,
            daily_pnl=self._daily_pnl,
            kill_switch=self._kill_switch,
        )

        if action == SignalAction.ENTER:
            self._handle_entry(details, snapshot, bar)

        elif action == SignalAction.ADD_LEVEL:
            self._handle_add(details, snapshot, bar)

        elif action == SignalAction.EXIT_FULL:
            return self._handle_exit(details, snapshot, bar)

        elif action == SignalAction.STOP:
            return self._handle_stop(details, snapshot, bar)

        return None

    def _handle_entry(
        self, details: dict, snapshot: SpreadSnapshot, bar: PairBar,
    ) -> None:
        """Handle a new cycle entry."""
        direction = details["direction"]
        level = details["level"]
        mult = details["mult"]

        cycle = self._cycle_mgr.open_cycle(direction, level, snapshot, mult)
        if not cycle:
            return

        # Simulate fills
        self._simulate_entry_fills(cycle, bar, direction)

    def _handle_add(
        self, details: dict, snapshot: SpreadSnapshot, bar: PairBar,
    ) -> None:
        """Handle adding a grid level."""
        level = details["level"]
        mult = details["mult"]

        grid_level = self._cycle_mgr.add_level(level, snapshot, mult)
        if not grid_level:
            return

        cycle = self._cycle_mgr.active_cycle
        if cycle:
            self._simulate_add_fills(cycle, grid_level, bar)

    def _handle_exit(
        self, details: dict, snapshot: SpreadSnapshot, bar: PairBar,
    ) -> TradeRecord | None:
        """Handle a take-profit exit."""
        reason = details.get("reason", StopReason.TAKE_PROFIT)
        z_exit = details.get("z_exit", snapshot.zscore)

        return self._close_cycle(reason, z_exit, bar)

    def _handle_stop(
        self, details: dict, snapshot: SpreadSnapshot, bar: PairBar,
    ) -> TradeRecord | None:
        """Handle a stop exit."""
        reason = details.get("reason", StopReason.STOP_LOSS_Z)
        z_exit = details.get("z_exit", snapshot.zscore)

        trade = self._close_cycle(reason, z_exit, bar)
        self._bars_since_last_stop = 0
        return trade

    def _close_cycle(
        self, reason: StopReason, z_exit: float, bar: PairBar,
    ) -> TradeRecord | None:
        """Close the active cycle and record the trade."""
        cycle = self._cycle_mgr.active_cycle
        if not cycle:
            return None

        # Simulate exit fills
        fees = self._simulate_exit_fills(cycle, bar)

        closed = self._cycle_mgr.close_cycle(reason, z_exit)
        if not closed:
            return None

        # Account for fill tracker fees
        total_fees = self._fill_tracker.get_total_fees(closed.cycle_id) + fees
        gross_pnl = closed.realized_pnl
        net_pnl = gross_pnl - total_fees

        # Update tracking
        self._cumulative_pnl += net_pnl
        self._daily_pnl += net_pnl
        if net_pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        self._fill_tracker.cleanup_cycle(closed.cycle_id)

        return TradeRecord(
            cycle_id=closed.cycle_id,
            direction=closed.direction.value if closed.direction else "unknown",
            levels_used=closed.max_level_filled,
            entry_z_avg=round(closed.entry_z_avg, 4),
            exit_z=round(z_exit, 4),
            bars_held=sum(lv.bars_held for lv in closed.levels),
            gross_pnl=round(gross_pnl, 4),
            fees=round(total_fees, 4),
            net_pnl=round(net_pnl, 4),
            entry_timestamp_ms=closed.opened_at_ms,
            exit_timestamp_ms=bar.timestamp_ms,
            stop_reason=reason.value,
        )

    def _force_close_cycle(self, bar: PairBar | None) -> TradeRecord | None:
        """Force-close an open cycle at end of simulation."""
        cycle = self._cycle_mgr.active_cycle
        if not cycle:
            return None

        if bar:
            fees = self._simulate_exit_fills(cycle, bar)
        else:
            fees = 0.0

        closed = self._cycle_mgr.close_cycle(StopReason.END_OF_SESSION, 0.0)
        if not closed:
            return None

        total_fees = fees
        gross_pnl = closed.realized_pnl
        net_pnl = gross_pnl - total_fees
        self._cumulative_pnl += net_pnl

        return TradeRecord(
            cycle_id=closed.cycle_id,
            direction=closed.direction.value if closed.direction else "unknown",
            levels_used=closed.max_level_filled,
            entry_z_avg=round(closed.entry_z_avg, 4),
            exit_z=0.0,
            bars_held=sum(lv.bars_held for lv in closed.levels),
            gross_pnl=round(gross_pnl, 4),
            fees=round(total_fees, 4),
            net_pnl=round(net_pnl, 4),
            entry_timestamp_ms=closed.opened_at_ms,
            exit_timestamp_ms=bar.timestamp_ms if bar else 0,
            stop_reason="end_of_session",
        )

    def _simulate_entry_fills(
        self, cycle: CycleState, bar: PairBar, direction: Direction,
    ) -> None:
        """Simulate fill for entry orders."""
        for lv in cycle.levels:
            if not lv.filled:
                continue

            left_side = OrderSide.BUY if direction == Direction.LONG_SPREAD else OrderSide.SELL
            right_side = OrderSide.SELL if direction == Direction.LONG_SPREAD else OrderSide.BUY

            self._fill_tracker.simulate_fill(
                order_id=f"{cycle.cycle_id}-L{lv.level}-left",
                cycle_id=cycle.cycle_id,
                symbol=self._config.symbols.left,
                side=left_side,
                qty=lv.size_left,
                mid_price=bar.left.close,
            )
            self._fill_tracker.simulate_fill(
                order_id=f"{cycle.cycle_id}-L{lv.level}-right",
                cycle_id=cycle.cycle_id,
                symbol=self._config.symbols.right,
                side=right_side,
                qty=lv.size_right,
                mid_price=bar.right.close,
            )

    def _simulate_add_fills(
        self, cycle: CycleState, grid_level: object, bar: PairBar,
    ) -> None:
        """Simulate fill for add-level orders."""
        from hyperoil.types import GridLevel
        lv: GridLevel = grid_level  # type: ignore[assignment]
        direction = cycle.direction

        left_side = OrderSide.BUY if direction == Direction.LONG_SPREAD else OrderSide.SELL
        right_side = OrderSide.SELL if direction == Direction.LONG_SPREAD else OrderSide.BUY

        self._fill_tracker.simulate_fill(
            order_id=f"{cycle.cycle_id}-L{lv.level}-left",
            cycle_id=cycle.cycle_id,
            symbol=self._config.symbols.left,
            side=left_side,
            qty=lv.size_left,
            mid_price=bar.left.close,
        )
        self._fill_tracker.simulate_fill(
            order_id=f"{cycle.cycle_id}-L{lv.level}-right",
            cycle_id=cycle.cycle_id,
            symbol=self._config.symbols.right,
            side=right_side,
            qty=lv.size_right,
            mid_price=bar.right.close,
        )

    def _simulate_exit_fills(self, cycle: CycleState, bar: PairBar) -> float:
        """Simulate exit fills and return total fees."""
        direction = cycle.direction
        # Exit is opposite direction
        left_side = OrderSide.SELL if direction == Direction.LONG_SPREAD else OrderSide.BUY
        right_side = OrderSide.BUY if direction == Direction.LONG_SPREAD else OrderSide.SELL

        total_fees = 0.0
        for lv in cycle.levels:
            if not lv.filled:
                continue

            f_left = self._fill_tracker.simulate_fill(
                order_id=f"{cycle.cycle_id}-L{lv.level}-exit-left",
                cycle_id=cycle.cycle_id,
                symbol=self._config.symbols.left,
                side=left_side,
                qty=lv.size_left,
                mid_price=bar.left.close,
            )
            f_right = self._fill_tracker.simulate_fill(
                order_id=f"{cycle.cycle_id}-L{lv.level}-exit-right",
                cycle_id=cycle.cycle_id,
                symbol=self._config.symbols.right,
                side=right_side,
                qty=lv.size_right,
                mid_price=bar.right.close,
            )
            total_fees += f_left.fee + f_right.fee

        return total_fees
