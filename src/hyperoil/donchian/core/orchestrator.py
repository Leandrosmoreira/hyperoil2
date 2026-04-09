"""Live orchestrator for the Donchian Ensemble strategy.

Wires together every Sprint 1-4 component into a single async loop:

    MultiAssetWsFeed → DonchianSignalEngine → RiskParityEngine
                                            → VolatilityTargetEngine
                                            → DonchianPositionSizer
                                            → DonchianDecisionEngine
                                            → PortfolioManager
                                            → SingleOrderManager → HyperliquidClient

Lifecycle (`start`):
    1. Initialize SQLite + JSONL writer.
    2. Load ticker_mapping.json (sz_decimals + api max leverage).
    3. Connect HyperliquidClient (paper or live).
    4. Warm up DonchianSignalEngine from historical 4h candles in the
       shared SQLite (Sprint 1 storage).
    5. Start the health server on `observability.health_port` (default 9091).
    6. Start the multi-asset WebSocket feed.
    7. Wait for shutdown signal (SIGINT/SIGTERM or kill switch).

Per-bar pipeline (`_process_bar`):
    Triggered when ANY symbol's 4h candle closes. The pipeline is serialized
    by an asyncio.Lock so concurrent bar-close callbacks for different
    symbols don't interleave with each other. The pipeline mirrors the
    backtest simulator one-for-one — same vols / weights / decisions /
    portfolio updates — but the EXIT/ENTER decisions are routed through
    SingleOrderManager instead of PortfolioManager-only state changes.

Project rules honored:
    - Every entry / exit / increase / decrease is maker post-only by default
      (SingleOrderManager handles the "Alo" TIF). Only `stop_hit` exits
      route through the emergency taker path, and only if the policy says so.
    - Kill switch is checked on every pipeline run — when active, all
      positions are forcibly flat-closed via the order manager.
    - Graceful shutdown closes every open position with the order manager
      before tearing down the WebSocket and DB.
"""

from __future__ import annotations

import asyncio
import json
import math
import signal
import sys
from pathlib import Path
from typing import Any

import numpy as np

from hyperoil.config import EnvConfig, ExecutionConfig, MarketDataConfig
from hyperoil.donchian.config import DonchianAppConfig
from hyperoil.donchian.core.ws_multi_feed import MultiAssetWsFeed
from hyperoil.donchian.data.storage import load_candles_from_db
from hyperoil.donchian.execution.order_manager import SingleOrderManager
from hyperoil.donchian.signals.signal_engine import DonchianSignalEngine
from hyperoil.donchian.sizing.position_sizer import (
    DonchianPositionSizer,
    compute_portfolio_targets,
)
from hyperoil.donchian.sizing.risk_parity import RiskParityEngine, compute_realized_vol
from hyperoil.donchian.sizing.vol_target import VolatilityTargetEngine
from hyperoil.donchian.strategy.decision_engine import Decision, DonchianDecisionEngine
from hyperoil.donchian.strategy.portfolio_manager import PortfolioManager
from hyperoil.donchian.types import AssetClass, DonchianAction
from hyperoil.execution.client import HyperliquidClient
from hyperoil.observability.health import start_health_server
from hyperoil.observability.logger import get_logger
from hyperoil.risk.kill_switch import KillSwitch
from hyperoil.storage.database import close_db, init_db
from hyperoil.storage.jsonl_writer import JsonlWriter

log = get_logger(__name__)


class DonchianOrchestrator:
    """Live async loop for the Donchian Ensemble strategy."""

    def __init__(
        self,
        cfg: DonchianAppConfig,
        env: EnvConfig,
        ticker_mapping_path: str = "config/ticker_mapping.json",
    ) -> None:
        self.cfg = cfg
        self.env = env
        self.ticker_mapping_path = ticker_mapping_path

        # --- Shared infra ---
        self.jsonl = JsonlWriter(cfg.storage.jsonl_dir)
        self.kill_switch = KillSwitch()
        self._shutdown_event = asyncio.Event()
        self._pipeline_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[Any]] = []
        self._health_runner: Any = None

        # --- Universe + asset class lookup ---
        self.symbols: list[str] = [
            f"{a.dex_prefix}:{a.hl_ticker}" for a in cfg.universe.assets
        ]
        self.asset_classes: dict[str, AssetClass] = {
            f"{a.dex_prefix}:{a.hl_ticker}": a.asset_class for a in cfg.universe.assets
        }

        # --- Static metadata loaded from ticker_mapping.json ---
        self._sz_decimals: dict[str, int] = {}      # keyed by HL ticker (no prefix)
        self._api_max_leverage: dict[str, float] = {}  # keyed by dex_symbol

        # --- Engines (Sprint 2-4) ---
        self.signal_engine = DonchianSignalEngine(
            lookbacks=cfg.signal.lookbacks,
            ema_period=cfg.signal.ema_period,
            min_score_entry=cfg.signal.min_score_entry,
        )
        self.risk_parity = RiskParityEngine()
        self.vol_target = VolatilityTargetEngine(
            vol_target_annual=cfg.sizing.vol_target_annual,
            vol_factor_cap=cfg.sizing.vol_factor_cap,
        )
        self.sizer = DonchianPositionSizer(sizing_cfg=cfg.sizing, risk_cfg=cfg.risk)
        self.decision_engine = DonchianDecisionEngine(
            signal_cfg=cfg.signal,
            risk_cfg=cfg.risk,
            risk_parity_cfg=cfg.risk_parity,
        )
        self.portfolio = PortfolioManager(
            initial_capital=cfg.backtest.initial_capital,
            fee_maker_bps=cfg.backtest.fee_maker_bps,
            fee_taker_bps=cfg.backtest.fee_taker_bps,
            slippage_bps=cfg.backtest.slippage_bps,
        )

        # Vol window in 4h bars (cfg uses days).
        self.vol_window_4h = max(2, cfg.risk_parity.vol_window * 6)
        self._closes_buffer: dict[str, list[float]] = {s: [] for s in self.symbols}

        # --- Execution stack (built in start()) ---
        self._client: HyperliquidClient | None = None
        self._order_mgr: SingleOrderManager | None = None
        self._ws_feed: MultiAssetWsFeed | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        log.info(
            "donchian_orchestrator_starting",
            mode=self.cfg.execution_mode,
            n_assets=len(self.symbols),
        )

        # 1) Persistent storage
        await init_db(self.cfg.storage.sqlite_path)

        # 2) Static metadata
        self._load_ticker_mapping()

        # 3) Execution client
        execution_cfg = ExecutionConfig(mode=self.cfg.execution_mode)
        self._client = HyperliquidClient(
            config=execution_cfg,
            private_key=self.env.hyperliquid_private_key,
            wallet_address=self.env.hyperliquid_wallet_address,
            api_url=self.env.hyperliquid_api_url,
        )
        await self._client.connect()
        self._order_mgr = SingleOrderManager(
            client=self._client,
            policy=self.cfg.order_policy,
            sz_decimals=self._sz_decimals,
        )

        # 4) Warmup signal engine + closes buffer from SQLite history
        await self._warmup_from_db()

        # 5) Health server (different port from pair trading)
        self._health_runner = await start_health_server(
            self.cfg.observability.health_port,
        )

        # 6) Signal handlers (POSIX only — Windows uses KeyboardInterrupt)
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._request_shutdown)

        # 7) Start the multi-asset WebSocket feed
        self._ws_feed = MultiAssetWsFeed(
            assets=self.cfg.universe.assets,
            market_data=MarketDataConfig(),  # use defaults — Donchian needs no overrides
            on_bar_close=self._on_bar_close,
            interval=self.cfg.signal.interval,
        )
        await self._ws_feed.start()

        # 8) Background loops
        self._tasks.append(asyncio.create_task(self._kill_switch_loop()))

        log.info(
            "donchian_orchestrator_started",
            n_assets=len(self.symbols),
            health_port=self.cfg.observability.health_port,
            initial_capital=self.cfg.backtest.initial_capital,
        )

        await self._shutdown_event.wait()
        await self._shutdown()

    def _request_shutdown(self) -> None:
        log.info("donchian_shutdown_requested")
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        log.info("donchian_orchestrator_shutting_down")

        # Force-close any open positions through the order manager (paper or live).
        await self._flat_all("shutdown")

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._ws_feed is not None:
            await self._ws_feed.stop()
        if self._client is not None:
            await self._client.disconnect()
        if self._health_runner is not None:
            await self._health_runner.cleanup()

        await close_db()

        await self.jsonl.write_incident(
            incident_type="donchian_shutdown",
            severity="info",
            equity=round(self.portfolio.equity, 2),
            n_positions=self.portfolio.n_open_positions(),
        )
        log.info(
            "donchian_orchestrator_stopped",
            final_equity=round(self.portfolio.equity, 2),
            n_open=self.portfolio.n_open_positions(),
        )

    # ------------------------------------------------------------------
    # Static metadata
    # ------------------------------------------------------------------
    def _load_ticker_mapping(self) -> None:
        path = Path(self.ticker_mapping_path)
        if not path.exists():
            log.warning("donchian_ticker_mapping_missing", path=str(path))
            return
        raw = json.loads(path.read_text())
        for ticker, info in raw.get("ticker_mapping", {}).items():
            sz = info.get("sz_decimals")
            if sz is not None:
                self._sz_decimals[ticker] = int(sz)
            sym = info.get("dex_symbol")
            lev = info.get("max_leverage")
            if sym and lev is not None:
                self._api_max_leverage[sym] = float(lev)
        log.info(
            "donchian_ticker_mapping_loaded",
            n_sz=len(self._sz_decimals),
            n_lev=len(self._api_max_leverage),
        )

    # ------------------------------------------------------------------
    # Warmup
    # ------------------------------------------------------------------
    async def _warmup_from_db(self) -> None:
        """Seed signal engine + closes buffer from the SQLite candle history.

        We need at least max(lookbacks) + 1 bars per symbol for the engine to
        be warm. Pull a generous extra to leave room for the closes vol
        window. If a symbol has no history we log a warning and continue —
        it will be skipped by the pipeline until the WS feed delivers enough
        live bars.
        """
        needed = max(self.cfg.signal.lookbacks) + self.vol_window_4h + 100
        for sym in self.symbols:
            df = await load_candles_from_db(symbol=sym, interval=self.cfg.signal.interval)
            if df.empty:
                log.warning("donchian_warmup_empty", symbol=sym)
                continue
            df = df.tail(needed)
            bars = [
                {
                    "timestamp_ms": int(row.timestamp_ms),
                    "open": float(row.open),
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                }
                for row in df.itertuples(index=False)
            ]
            self.signal_engine.seed_history(sym, bars)
            self._closes_buffer[sym] = [b["close"] for b in bars]
            log.info(
                "donchian_warmup_loaded",
                symbol=sym,
                bars=len(bars),
                warm=self.signal_engine.is_warm(sym),
            )

    # ------------------------------------------------------------------
    # Bar-close callback
    # ------------------------------------------------------------------
    async def _on_bar_close(self, symbol: str, bar: dict[str, Any]) -> None:
        """Receive a freshly closed 4h candle for one symbol.

        Pushes the bar into the engine + closes buffer, persists it to
        JSONL, then runs the per-bar pipeline under a lock so concurrent
        callbacks for different symbols can't interleave.
        """
        # Persist BEFORE pipeline so we never lose a candle even if the
        # pipeline raises.
        await self.jsonl.write_candle(
            symbol=symbol,
            timestamp_ms=int(bar["timestamp_ms"]),
            open=float(bar["open"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            close=float(bar["close"]),
            volume=float(bar.get("volume", 0.0) or 0.0),
            interval=self.cfg.signal.interval,
        )

        async with self._pipeline_lock:
            self.signal_engine.update_bar(symbol, bar)
            buf = self._closes_buffer.setdefault(symbol, [])
            buf.append(float(bar["close"]))
            cap = self.vol_window_4h + 50
            if len(buf) > cap:
                del buf[: len(buf) - cap]

            if not self.signal_engine.is_warm(symbol):
                return

            await self._process_bar(int(bar["timestamp_ms"]))

    # ------------------------------------------------------------------
    # Per-bar pipeline (mirrors DonchianSimulator._process_bar)
    # ------------------------------------------------------------------
    async def _process_bar(self, ts_ms: int) -> None:
        # Mark to market with the latest closes from each per-symbol buffer.
        prices: dict[str, float] = {}
        for sym, closes in self._closes_buffer.items():
            if closes:
                prices[sym] = closes[-1]
        if not prices:
            return

        self.portfolio.update_prices(prices, timestamp_ms=ts_ms)

        # Kill switch shutdown — flat everything and bail.
        if self.kill_switch.is_active:
            log.warning("donchian_kill_switch_flat_all", reason=self.kill_switch.reason)
            await self._flat_all("kill_switch")
            return

        signals = self.signal_engine.compute_all()
        if not signals:
            return

        self.portfolio.update_trailing_stops(signals)

        vols: dict[str, float] = {}
        for sym, closes in self._closes_buffer.items():
            v = compute_realized_vol(np.asarray(closes, dtype=float), window=self.vol_window_4h)
            if math.isfinite(v):
                vols[sym] = float(v)

        scores = {sym: sig.score for sym, sig in signals.items()}
        targets = compute_portfolio_targets(
            vols=vols,
            scores=scores,
            asset_classes=self.asset_classes,
            api_max_leverage=self._api_max_leverage,
            capital=self.portfolio.equity,
            drawdown_pct=self.portfolio.drawdown_pct,
            risk_parity=self.risk_parity,
            vol_target=self.vol_target,
            sizer=self.sizer,
        )

        decisions = self.decision_engine.evaluate(
            positions=self.portfolio.positions,
            signals=signals,
            targets=targets,
            prices=prices,
            drawdown_pct=self.portfolio.drawdown_pct,
        )

        for d in decisions:
            if d.action == DonchianAction.HOLD:
                continue
            await self._apply_decision(d, ts_ms)

        log.info(
            "donchian_pipeline_tick",
            ts=ts_ms,
            n_signals=len(signals),
            n_decisions=sum(1 for d in decisions if d.action != DonchianAction.HOLD),
            equity=round(self.portfolio.equity, 2),
            dd_pct=round(self.portfolio.drawdown_pct * 100, 2),
            n_open=self.portfolio.n_open_positions(),
        )

    # ------------------------------------------------------------------
    # Decision execution — routes through SingleOrderManager and only
    # mutates the PortfolioManager on success.
    # ------------------------------------------------------------------
    async def _apply_decision(self, d: Decision, ts_ms: int) -> None:
        if self._order_mgr is None:
            return
        if d.price <= 0:
            return

        sym = d.symbol
        try:
            if d.action == DonchianAction.ENTER:
                outcome = await self._order_mgr.execute_enter(
                    symbol=sym, notional_usd=d.target_size_usd, price=d.price,
                )
                if outcome.success:
                    self.portfolio.open_position(
                        symbol=sym, size_usd=d.target_size_usd, price=d.price,
                        score=d.score, leverage=d.leverage, stop_line=d.stop_line,
                        timestamp_ms=ts_ms, is_maker=True,
                    )
            elif d.action == DonchianAction.INCREASE:
                delta = max(0.0, d.target_size_usd - d.current_size_usd)
                if delta <= 0:
                    return
                outcome = await self._order_mgr.execute_increase(
                    symbol=sym, delta_notional_usd=delta, price=d.price,
                )
                if outcome.success:
                    self.portfolio.increase_position(
                        symbol=sym, new_size_usd=d.target_size_usd, price=d.price,
                        timestamp_ms=ts_ms, is_maker=True,
                    )
            elif d.action == DonchianAction.DECREASE:
                delta = max(0.0, d.current_size_usd - d.target_size_usd)
                if delta <= 0:
                    return
                outcome = await self._order_mgr.execute_decrease(
                    symbol=sym, delta_notional_usd=delta, price=d.price,
                )
                if outcome.success:
                    self.portfolio.decrease_position(
                        symbol=sym, new_size_usd=d.target_size_usd, price=d.price,
                        timestamp_ms=ts_ms, is_maker=True,
                    )
            elif d.action == DonchianAction.EXIT:
                outcome = await self._order_mgr.execute_exit(
                    symbol=sym, notional_usd=d.current_size_usd, price=d.price,
                    reason=d.reason,
                )
                if outcome.success:
                    self.portfolio.close_position(
                        symbol=sym, price=d.price, timestamp_ms=ts_ms,
                        is_maker=(d.reason != "stop_hit"), reason=d.reason,
                    )
            else:
                return

            await self.jsonl.write_trade(
                ts=ts_ms,
                symbol=sym,
                action=d.action.value,
                reason=d.reason,
                price=d.price,
                target_size_usd=d.target_size_usd,
                current_size_usd=d.current_size_usd,
                score=d.score,
                leverage=d.leverage,
                success=outcome.success,
                error=outcome.error,
                exchange_oid=outcome.exchange_oid,
            )
        except Exception:
            log.exception("donchian_apply_decision_failed", symbol=sym, action=d.action.value)

    # ------------------------------------------------------------------
    # Force-close all open positions (kill switch + shutdown path)
    # ------------------------------------------------------------------
    async def _flat_all(self, reason: str) -> None:
        if self._order_mgr is None:
            return
        for sym in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[sym]
            price = pos.current_price if pos.current_price > 0 else pos.entry_price
            try:
                outcome = await self._order_mgr.execute_exit(
                    symbol=sym, notional_usd=pos.size_usd, price=price, reason=reason,
                )
                if outcome.success:
                    self.portfolio.close_position(
                        symbol=sym, price=price,
                        timestamp_ms=self.portfolio.state.timestamp_ms,
                        is_maker=False, reason=reason,
                    )
            except Exception:
                log.exception("donchian_flat_all_failed", symbol=sym, reason=reason)

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------
    async def _kill_switch_loop(self) -> None:
        """Poll the kill switch every second; trigger flat-all on activation."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(1.0)
                if self.kill_switch.is_active and self.portfolio.n_open_positions() > 0:
                    async with self._pipeline_lock:
                        await self._flat_all("kill_switch")
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("donchian_kill_switch_loop_error")
                await asyncio.sleep(5.0)
