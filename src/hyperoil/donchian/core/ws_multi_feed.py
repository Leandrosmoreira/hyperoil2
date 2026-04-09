"""Multi-symbol WebSocket feed for the Donchian Ensemble strategy.

Subscribes to ``candle`` updates for every symbol in the Donchian universe
(25 assets across 5 classes) and emits a callback exactly once per symbol
when a 4h candle CLOSES (i.e. when ``T`` advances). This is the trigger the
orchestrator hangs all of its work off — signals, sizing, decisions, orders.

Hyperliquid sends one ``candle`` message per ~1s with the OPEN bar's running
OHLCV. The bar is "closed" when a new ``T`` (close-time) is observed for
that symbol — at that point the previous bar is final and the orchestrator
can absorb it.

Behaviour
---------
- One persistent WebSocket. We do NOT shard 25 symbols across multiple
  connections — the HL endpoint handles 25 candle subs comfortably.
- State machine identical to the pair-trading WsFeed: DISCONNECTED →
  CONNECTING → SUBSCRIBING → CONNECTED → STALE. Same exponential backoff.
- Stale detector forces a reconnect after 2× ``stale_timeout_sec`` without
  any inbound message — same rule as the pair feed (which fixed bug #5 in
  the v2 incident log).
- ``on_bar_close(symbol, bar_dict)`` is awaited once per closed bar. Bars
  are dicts (``timestamp_ms / open / high / low / close / volume``) so they
  can be passed straight to ``DonchianSignalEngine.update_bar``.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from hyperoil.config import MarketDataConfig
from hyperoil.donchian.config import AssetConfig
from hyperoil.observability.logger import get_logger
from hyperoil.types import ConnectionState

log = get_logger(__name__)

BarCloseCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class MultiAssetWsFeed:
    """One WebSocket, N candle subscriptions, fires bar-close callbacks."""

    WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(
        self,
        assets: list[AssetConfig],
        market_data: MarketDataConfig,
        on_bar_close: BarCloseCallback,
        interval: str = "4h",
    ) -> None:
        if not assets:
            raise ValueError("assets must not be empty")
        self._assets = assets
        self._cfg = market_data
        self._on_bar_close = on_bar_close
        self._interval = interval

        # dex symbols, e.g. "xyz:BTC", "hyna:ETH"
        self._dex_symbols = [f"{a.dex_prefix}:{a.hl_ticker}" for a in assets]

        self._state = ConnectionState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._running = False
        self._last_msg_time = 0.0
        self._reconnect_delay = market_data.reconnect_delay_initial_sec

        # Per-symbol last close-time. A bar is "closed" when we see a NEW T
        # for that symbol, at which point we emit the *previous* finished bar.
        self._last_close_time: dict[str, int] = {}
        # Buffer of the most-recent partial bar per symbol. Promoted to
        # closed when T advances.
        self._partial: dict[str, dict[str, Any]] = {}

        self._ws_task: asyncio.Task[Any] | None = None
        self._stale_task: asyncio.Task[Any] | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def symbols(self) -> list[str]:
        return list(self._dex_symbols)

    @property
    def n_subscribed(self) -> int:
        return len(self._last_close_time)

    @property
    def last_msg_time(self) -> float:
        return self._last_msg_time

    def _set_state(self, new_state: ConnectionState) -> None:
        if new_state != self._state:
            log.info("ws_multi_state", old=self._state.value, new=new_state.value)
            self._state = new_state

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        self._ws_task = asyncio.create_task(self._run_loop())
        self._stale_task = asyncio.create_task(self._stale_detector())
        log.info("ws_multi_started", n_symbols=len(self._dex_symbols))

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        for task in (self._ws_task, self._stale_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._set_state(ConnectionState.DISCONNECTED)
        log.info("ws_multi_stopped")

    # ------------------------------------------------------------------
    # Connection loop
    # ------------------------------------------------------------------
    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("ws_multi_connection_error")

            if not self._running:
                break

            self._set_state(ConnectionState.RECONNECTING)
            log.info("ws_multi_reconnecting", delay=self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2,
                self._cfg.reconnect_delay_max_sec,
            )

    async def _connect_and_listen(self) -> None:
        self._set_state(ConnectionState.CONNECTING)

        async with websockets.connect(
            self.WS_URL,
            ping_interval=self._cfg.ws_ping_interval_sec,
            ping_timeout=self._cfg.ws_ping_timeout_sec,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._set_state(ConnectionState.SUBSCRIBING)

            for sym in self._dex_symbols:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {
                        "type": "candle",
                        "coin": sym,
                        "interval": self._interval,
                    },
                }))

            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_delay = self._cfg.reconnect_delay_initial_sec
            log.info("ws_multi_connected", symbols=len(self._dex_symbols), interval=self._interval)

            async for raw in ws:
                if not self._running:
                    break
                self._last_msg_time = time.time()
                if self._state == ConnectionState.STALE:
                    self._set_state(ConnectionState.CONNECTED)

                try:
                    msg = json.loads(raw)
                    await self._handle_message(msg)
                except json.JSONDecodeError:
                    log.warning("ws_multi_invalid_json", raw=str(raw)[:200])
                except Exception:
                    log.exception("ws_multi_handler_error")

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------
    async def _handle_message(self, msg: dict[str, Any]) -> None:
        channel = msg.get("channel")
        if channel == "candle":
            await self._handle_candle(msg.get("data", {}))
        elif channel == "subscriptionResponse":
            log.debug("ws_multi_sub_ack", data=msg)

    async def _handle_candle(self, data: dict[str, Any]) -> None:
        sym = data.get("s") or ""
        close_time = int(data.get("T", 0) or 0)
        if not sym or close_time == 0:
            return

        try:
            bar = {
                "timestamp_ms": close_time,
                "open": float(data["o"]),
                "high": float(data["h"]),
                "low": float(data["l"]),
                "close": float(data["c"]),
                "volume": float(data.get("v", 0) or 0.0),
            }
        except (KeyError, TypeError, ValueError):
            log.warning("ws_multi_candle_parse_error", data=data)
            return

        prev_T = self._last_close_time.get(sym)
        partial = self._partial.get(sym)

        if prev_T is None:
            # First message for this symbol — just seed state, nothing to emit yet.
            self._last_close_time[sym] = close_time
            self._partial[sym] = bar
            return

        if close_time > prev_T:
            # The previous T's bar is now FINAL. Emit it and start tracking the new T.
            if partial is not None:
                final_bar = dict(partial)
                final_bar["timestamp_ms"] = prev_T
                try:
                    await self._on_bar_close(sym, final_bar)
                except Exception:
                    log.exception("ws_multi_bar_close_callback_error", symbol=sym)
            self._last_close_time[sym] = close_time
            self._partial[sym] = bar
        else:
            # Same bar still open — update the partial buffer in place.
            self._partial[sym] = bar

    # ------------------------------------------------------------------
    # Stale detection (same logic as the pair-trading feed)
    # ------------------------------------------------------------------
    async def _stale_detector(self) -> None:
        timeout = self._cfg.stale_timeout_sec
        stale_since = 0.0
        while self._running:
            try:
                await asyncio.sleep(5.0)

                if self._state not in (ConnectionState.CONNECTED, ConnectionState.STALE):
                    stale_since = 0.0
                    continue

                if self._last_msg_time == 0:
                    continue

                elapsed = time.time() - self._last_msg_time
                if elapsed > timeout:
                    if self._state != ConnectionState.STALE:
                        self._set_state(ConnectionState.STALE)
                        stale_since = time.time()
                        log.warning(
                            "ws_multi_stale_detected",
                            elapsed_sec=round(elapsed, 1),
                            timeout=timeout,
                        )
                    if time.time() - stale_since > timeout * 2:
                        log.error("ws_multi_stale_too_long")
                        if self._ws is not None:
                            try:
                                await self._ws.close()
                            except Exception:
                                pass
                        stale_since = 0.0

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("ws_multi_stale_detector_error")
                await asyncio.sleep(10.0)
