"""Main orchestrator — wires all modules and runs the async event loop.

This is the central nervous system: market data → signals → strategy →
execution → risk, all running under one asyncio loop.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

from hyperoil.config import AppConfig, EnvConfig
from hyperoil.core.event_bus import EventBus
from hyperoil.core.state import AppState
from hyperoil.execution.client import HyperliquidClient
from hyperoil.execution.fill_tracker import FillTracker
from hyperoil.execution.hedge_emergency import HedgeEmergency
from hyperoil.execution.order_manager import OrderManager
from hyperoil.execution.reconcile import Reconciler
from hyperoil.market_data.ws_feed import WsFeed
from hyperoil.observability.dashboard import DashboardManager
from hyperoil.observability.health import start_health_server, update_health
from hyperoil.observability.logger import get_logger
from hyperoil.risk.exposure import ExposureTracker
from hyperoil.risk.gate import RiskGate
from hyperoil.risk.kill_switch import KillSwitch
from hyperoil.signals.signal_engine import SignalEngine
from hyperoil.storage.database import close_db, init_db
from hyperoil.storage.jsonl_writer import JsonlWriter
from hyperoil.strategy.grid_pairs import GridDecisionEngine
from hyperoil.strategy.lifecycle import CycleManager
from hyperoil.types import (
    ConnectionState,
    Direction,
    SignalAction,
    StopReason,
    now_ms,
)

log = get_logger(__name__)


class Orchestrator:
    """Main application orchestrator. Wires modules, manages lifecycle."""

    def __init__(self, config: AppConfig, env: EnvConfig) -> None:
        self.config = config
        self.env = env
        self.state = AppState()
        self.event_bus = EventBus()
        self.jsonl = JsonlWriter(config.storage.jsonl_dir)
        self._shutdown_event = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._health_runner: Any = None

        # --- Module initialization ---
        self._signal_engine = SignalEngine(config.signal)
        self._decision_engine = GridDecisionEngine(config.grid, config.risk)
        self._cycle_mgr = CycleManager(config.sizing, config.grid)
        self._kill_switch = KillSwitch()
        self._exposure = ExposureTracker(config.risk)
        self._risk_gate = RiskGate(config.risk, self._exposure, self._kill_switch)
        self._fill_tracker = FillTracker(config.backtest)

        # Execution (initialized in start)
        self._client: HyperliquidClient | None = None
        self._order_mgr: OrderManager | None = None
        self._reconciler: Reconciler | None = None
        self._hedge_emergency: HedgeEmergency | None = None

        # Dashboard
        self._dashboard: DashboardManager | None = None
        if config.observability.dashboard_enabled:
            self._dashboard = DashboardManager(config.observability.dashboard_refresh_ms)

        # Market data feed (initialized in start)
        self._ws_feed: WsFeed | None = None

    async def start(self) -> None:
        """Initialize all modules and start the main loop."""
        log.info(
            "orchestrator_starting",
            mode=self.config.execution.mode,
            symbols_left=self.config.symbols.left,
            symbols_right=self.config.symbols.right,
        )

        # Initialize storage
        await init_db(self.config.storage.sqlite_path)

        # Load state snapshot if exists
        self.state.load_snapshot("data/state_snapshot.json")

        # Initialize execution client
        self._client = HyperliquidClient(
            config=self.config.execution,
            private_key=self.env.hyperliquid_private_key,
            wallet_address=self.env.hyperliquid_wallet_address,
            api_url=self.env.hyperliquid_api_url,
        )
        await self._client.connect()

        self._order_mgr = OrderManager(
            client=self._client,
            config=self.config.execution,
            symbols=self.config.symbols,
        )
        self._reconciler = Reconciler(
            client=self._client,
            order_manager=self._order_mgr,
        )
        self._hedge_emergency = HedgeEmergency(
            client=self._client,
            order_manager=self._order_mgr,
            config=self.config.execution,
            jsonl_writer=self.jsonl,
        )

        # Start health server
        self._health_runner = await start_health_server(
            self.config.observability.health_port,
        )

        # Register signal handlers for graceful shutdown
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._request_shutdown)

        # Start market data feed
        self._ws_feed = WsFeed(
            symbols=self.config.symbols,
            market_data=self.config.market_data,
            on_tick=self._on_tick,
            on_candle=self._on_candle,
        )
        await self._ws_feed.start()

        # Start background tasks
        self._tasks.append(asyncio.create_task(self._health_loop()))
        self._tasks.append(asyncio.create_task(self._state_snapshot_loop()))

        log.info("orchestrator_started")

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Graceful shutdown
        await self._shutdown()

    def _request_shutdown(self) -> None:
        """Signal the orchestrator to shut down gracefully."""
        log.info("shutdown_requested")
        self._shutdown_event.set()

    async def _on_tick(self, tick: Any) -> None:
        """Called by WsFeed on each tick — update state."""
        self.state.last_tick_ms = tick.timestamp_ms
        if self._ws_feed:
            self.state.ws_state = self._ws_feed.state

    async def _on_candle(
        self,
        symbol: str,
        timestamp_ms: int,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        mid: float,
    ) -> None:
        """Called by WsFeed when a new candle closes."""
        self._signal_engine.add_candle(
            symbol=symbol,
            timestamp_ms=timestamp_ms,
            open=open,
            high=high,
            low=low,
            close=close,
            volume=volume,
            mid=mid,
        )
        await self.process_bar()

    async def process_bar(self) -> None:
        """Process a single bar through the full pipeline.

        Called by the market data feed when a new candle closes,
        or by the backtest simulator for each bar.
        """
        self._exposure.tick_bar()

        # Compute signals
        snapshot = self._signal_engine.compute()
        if snapshot is None:
            return

        # Update state
        self.state.current_z = snapshot.zscore
        self.state.current_spread = snapshot.spread
        self.state.current_correlation = snapshot.correlation
        self.state.current_regime = snapshot.regime

        # Update active cycle with new prices
        if self._cycle_mgr.has_open_cycle:
            self._cycle_mgr.update(snapshot)
            if self._cycle_mgr.active_cycle:
                self._exposure.update_unrealized(
                    self._cycle_mgr.active_cycle.unrealized_pnl,
                )

        # System health check
        health = self._risk_gate.is_system_healthy(snapshot)
        if not health.allowed:
            log.warning("system_unhealthy", reason=health.reason)
            if self._cycle_mgr.has_open_cycle:
                await self._close_cycle(StopReason.KILL_SWITCH, snapshot.zscore)
            return

        # Get decision from strategy engine
        action, details = self._decision_engine.evaluate(
            snapshot=snapshot,
            cycle=self._cycle_mgr.active_cycle,
            bars_since_last_stop=self._exposure.bars_since_last_stop,
            consecutive_losses=self._exposure.consecutive_losses,
            daily_pnl=self._exposure.daily_pnl,
            kill_switch=self._kill_switch.is_active,
        )

        # Validate through risk gate
        gate_result = self._risk_gate.check_action(
            action, snapshot, self._cycle_mgr.active_cycle,
        )
        if not gate_result.allowed and action in (SignalAction.ENTER, SignalAction.ADD_LEVEL):
            log.info("action_blocked_by_gate", action=action.value, reason=gate_result.reason)
            return

        # Execute action
        await self._execute_action(action, details, snapshot)

        # Update dashboard
        if self._dashboard:
            self._update_dashboard(snapshot)

    async def _execute_action(
        self,
        action: SignalAction,
        details: dict,
        snapshot: Any,
    ) -> None:
        """Execute a trading action."""
        if action == SignalAction.ENTER:
            await self._open_cycle(details, snapshot)

        elif action == SignalAction.ADD_LEVEL:
            await self._add_level(details, snapshot)

        elif action == SignalAction.EXIT_FULL:
            reason = details.get("reason", StopReason.TAKE_PROFIT)
            z_exit = details.get("z_exit", snapshot.zscore)
            await self._close_cycle(reason, z_exit)

        elif action == SignalAction.STOP:
            reason = details.get("reason", StopReason.STOP_LOSS_Z)
            z_exit = details.get("z_exit", snapshot.zscore)
            await self._close_cycle(reason, z_exit)

    async def _open_cycle(self, details: dict, snapshot: Any) -> None:
        """Open a new trading cycle."""
        direction = details["direction"]
        level = details["level"]
        mult = details["mult"]

        cycle = self._cycle_mgr.open_cycle(direction, level, snapshot, mult)
        if not cycle:
            return

        self.state.active_cycle = cycle

        # Send entry orders
        if self._order_mgr:
            group_id, left, right = await self._order_mgr.send_pair_entry(
                cycle_id=cycle.cycle_id,
                direction=direction,
                size_left=cycle.total_size_left,
                size_right=cycle.total_size_right,
                level=level,
            )

        # Track exposure
        notional_left = cycle.total_size_left * snapshot.price_left
        notional_right = cycle.total_size_right * snapshot.price_right
        self._exposure.record_cycle_open(notional_left, notional_right)

        await self.event_bus.emit("cycle_opened", cycle_id=cycle.cycle_id)

    async def _add_level(self, details: dict, snapshot: Any) -> None:
        """Add a grid level to the active cycle."""
        level = details["level"]
        mult = details["mult"]

        grid_level = self._cycle_mgr.add_level(level, snapshot, mult)
        if not grid_level:
            return

        cycle = self._cycle_mgr.active_cycle
        if cycle and self._order_mgr:
            direction = cycle.direction or Direction.LONG_SPREAD
            await self._order_mgr.send_pair_entry(
                cycle_id=cycle.cycle_id,
                direction=direction,
                size_left=grid_level.size_left,
                size_right=grid_level.size_right,
                level=level,
            )

        # Track additional exposure
        notional_left = grid_level.size_left * snapshot.price_left
        notional_right = grid_level.size_right * snapshot.price_right
        self._exposure.record_level_add(notional_left, notional_right)

    async def _close_cycle(self, reason: StopReason, z_exit: float) -> None:
        """Close the active cycle."""
        cycle = self._cycle_mgr.active_cycle
        if not cycle:
            return

        # Send exit orders
        if self._order_mgr and cycle.direction:
            await self._order_mgr.send_pair_exit(
                cycle_id=cycle.cycle_id,
                direction=cycle.direction,
                size_left=cycle.total_size_left,
                size_right=cycle.total_size_right,
            )

        closed = self._cycle_mgr.close_cycle(reason, z_exit)
        if not closed:
            return

        # Track P&L
        fees = self._fill_tracker.get_total_fees(closed.cycle_id)
        was_stop = reason != StopReason.TAKE_PROFIT
        self._exposure.record_cycle_close(
            realized_pnl=closed.realized_pnl,
            fees=fees,
            was_stop=was_stop,
        )

        # Update state
        self.state.active_cycle = None
        self.state.daily_pnl = self._exposure.daily_pnl
        self.state.daily_trades += 1
        self.state.consecutive_losses = self._exposure.consecutive_losses

        # Log trade
        await self.jsonl.write_trade(
            cycle_id=closed.cycle_id,
            direction=closed.direction.value if closed.direction else "unknown",
            levels=closed.max_level_filled,
            pnl=closed.realized_pnl,
            fees=fees,
            reason=reason.value,
        )

        # Cleanup
        self._fill_tracker.cleanup_cycle(closed.cycle_id)
        await self.event_bus.emit("cycle_closed", cycle_id=closed.cycle_id)

    def _update_dashboard(self, snapshot: Any) -> None:
        """Push latest data to the dashboard."""
        if not self._dashboard:
            return

        cycle = self._cycle_mgr.active_cycle
        self._dashboard.update(
            ws_state=self.state.ws_state,
            last_tick_ms=self.state.last_tick_ms,
            current_z=snapshot.zscore,
            current_spread=snapshot.spread,
            current_beta=snapshot.beta,
            current_correlation=snapshot.correlation,
            regime=snapshot.regime,
            price_left=snapshot.price_left,
            price_right=snapshot.price_right,
            cycle=cycle,
            daily_pnl=self._exposure.daily_pnl,
            unrealized_pnl=cycle.unrealized_pnl if cycle else 0.0,
            cumulative_pnl=self._exposure.daily_pnl,
            total_notional=self._exposure.total_notional,
            consecutive_losses=self._exposure.consecutive_losses,
            bars_since_last_stop=self._exposure.bars_since_last_stop,
            kill_switch_active=self._kill_switch.is_active,
            drawdown_usd=self._exposure.drawdown_usd,
            mode=self.config.execution.mode,
        )
        self._dashboard.refresh()

    async def _shutdown(self) -> None:
        """Graceful shutdown — close positions, save state, stop tasks."""
        log.info("orchestrator_shutting_down")

        # Force-close any active cycle
        if self._cycle_mgr.has_open_cycle:
            closed = self._cycle_mgr.force_close()
            if closed:
                log.warning(
                    "emergency_close_on_shutdown",
                    cycle_id=closed.cycle_id,
                    pnl=round(closed.realized_pnl, 2),
                )

        # Save final state snapshot
        self.state.save_snapshot("data/state_snapshot.json")

        # Cancel background tasks
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Stop market data feed
        if self._ws_feed:
            await self._ws_feed.stop()

        # Disconnect execution client
        if self._client:
            await self._client.disconnect()

        # Close health server
        if self._health_runner:
            await self._health_runner.cleanup()

        # Close database
        await close_db()

        # Log final incident
        await self.jsonl.write_incident(
            incident_type="shutdown",
            severity="info",
            daily_pnl=self.state.daily_pnl,
            daily_trades=self.state.daily_trades,
        )

        log.info(
            "orchestrator_stopped",
            daily_pnl=round(self.state.daily_pnl, 2),
            daily_trades=self.state.daily_trades,
        )

    async def _health_loop(self) -> None:
        """Periodically update health status."""
        while not self._shutdown_event.is_set():
            try:
                if self._ws_feed:
                    self.state.ws_state = self._ws_feed.state
                update_health(self.state.to_health())
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("health_loop_error")
                await asyncio.sleep(5.0)

    async def _state_snapshot_loop(self) -> None:
        """Periodically save state snapshot for crash recovery."""
        interval = self.config.storage.state_snapshot_interval_sec
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(interval)
                self.state.save_snapshot("data/state_snapshot.json")
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("state_snapshot_error")
                await asyncio.sleep(interval)
