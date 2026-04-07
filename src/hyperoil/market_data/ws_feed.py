"""Async WebSocket feed for Hyperliquid market data with state machine."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Coroutine

import websockets
from websockets.asyncio.client import ClientConnection

from hyperoil.config import MarketDataConfig, SymbolsConfig
from hyperoil.observability.logger import get_logger
from hyperoil.types import ConnectionState, Tick, now_ms

log = get_logger(__name__)

# Type for tick callback
TickCallback = Callable[[Tick], Coroutine[Any, Any, None]]


class WsFeed:
    """Async WebSocket feed with connection state machine.

    State machine:
        DISCONNECTED → CONNECTING → SUBSCRIBING → CONNECTED → RECONNECTING
                                                       ↓
                                                    STALE (no msg > stale_timeout)
    """

    WS_URL = "wss://api.hyperliquid.xyz/ws"

    def __init__(
        self,
        symbols: SymbolsConfig,
        market_data: MarketDataConfig,
        on_tick: TickCallback | None = None,
        on_candle: Callable[..., Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._symbols = symbols
        self._config = market_data
        self._on_tick = on_tick
        self._on_candle = on_candle

        # State
        self._state = ConnectionState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._running = False
        self._last_msg_time: float = 0.0
        self._reconnect_delay: float = market_data.reconnect_delay_initial_sec

        # Candle tracking — detect new candles by close_time changes
        self._last_close_time: dict[str, int] = {}

        # Latest mid prices from allMids
        self._mid_prices: dict[str, float] = {}

        # Tasks
        self._ws_task: asyncio.Task[Any] | None = None
        self._stale_task: asyncio.Task[Any] | None = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def last_msg_time(self) -> float:
        return self._last_msg_time

    @property
    def mid_prices(self) -> dict[str, float]:
        return dict(self._mid_prices)

    def _set_state(self, new_state: ConnectionState) -> None:
        if new_state != self._state:
            old = self._state
            self._state = new_state
            log.info("ws_state_change", old=old.value, new=new_state.value)

    def _coin_name(self, symbol: str) -> str:
        """Ensure symbol has dex prefix."""
        if ":" in symbol:
            return symbol
        return f"{self._symbols.dex_prefix}:{symbol}"

    async def start(self) -> None:
        """Start the WebSocket feed."""
        self._running = True
        self._ws_task = asyncio.create_task(self._run_loop())
        self._stale_task = asyncio.create_task(self._stale_detector())
        log.info("ws_feed_started")

    async def stop(self) -> None:
        """Stop the WebSocket feed gracefully."""
        self._running = False

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self._stale_task:
            self._stale_task.cancel()
            try:
                await self._stale_task
            except asyncio.CancelledError:
                pass

        self._set_state(ConnectionState.DISCONNECTED)
        log.info("ws_feed_stopped")

    async def _run_loop(self) -> None:
        """Main reconnection loop with exponential backoff."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("ws_connection_error")

            if not self._running:
                break

            self._set_state(ConnectionState.RECONNECTING)
            log.info("ws_reconnecting", delay=self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2,
                self._config.reconnect_delay_max_sec,
            )

    async def _connect_and_listen(self) -> None:
        """Connect, subscribe, and listen for messages."""
        self._set_state(ConnectionState.CONNECTING)

        async with websockets.connect(
            self.WS_URL,
            ping_interval=self._config.ws_ping_interval_sec,
            ping_timeout=self._config.ws_ping_timeout_sec,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._set_state(ConnectionState.SUBSCRIBING)

            # Subscribe to candles for both symbols
            await self._subscribe(ws, self._symbols.left)
            await self._subscribe(ws, self._symbols.right)

            # Subscribe to allMids for real-time bid/ask approximation
            await ws.send(json.dumps({
                "method": "subscribe",
                "subscription": {"type": "allMids"},
            }))

            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_delay = self._config.reconnect_delay_initial_sec

            log.info(
                "ws_connected",
                symbols=[self._symbols.left, self._symbols.right],
            )

            # Listen loop
            async for raw_msg in ws:
                if not self._running:
                    break
                self._last_msg_time = time.time()

                # Reset stale state if we were stale
                if self._state == ConnectionState.STALE:
                    self._set_state(ConnectionState.CONNECTED)

                try:
                    msg = json.loads(raw_msg)
                    await self._handle_message(msg)
                except json.JSONDecodeError:
                    log.warning("ws_invalid_json", raw=str(raw_msg)[:200])
                except Exception:
                    log.exception("ws_message_handler_error")

    async def _subscribe(self, ws: ClientConnection, symbol: str) -> None:
        """Subscribe to candle feed for a symbol."""
        coin = self._coin_name(symbol)
        msg = {
            "method": "subscribe",
            "subscription": {
                "type": "candle",
                "coin": coin,
                "interval": self._config.interval,
            },
        }
        await ws.send(json.dumps(msg))
        log.info("ws_subscribed", coin=coin, interval=self._config.interval)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Route incoming WebSocket message."""
        channel = msg.get("channel")

        if channel == "candle":
            await self._handle_candle(msg.get("data", {}))
        elif channel == "allMids":
            self._handle_all_mids(msg.get("data", {}))
        elif channel == "subscriptionResponse":
            log.debug("ws_subscription_ack", data=msg)
        # Silently ignore other message types

    async def _handle_candle(self, data: dict[str, Any]) -> None:
        """Process a candle update."""
        symbol_raw = data.get("s", "")
        close_time = data.get("T", 0)

        if not symbol_raw or not close_time:
            return

        # Extract clean symbol name
        symbol = symbol_raw.split(":")[-1] if ":" in symbol_raw else symbol_raw

        # Parse candle data
        try:
            open_price = float(data["o"])
            high = float(data["h"])
            low = float(data["l"])
            close = float(data["c"])
            volume = float(data.get("v", 0))
        except (KeyError, ValueError, TypeError):
            log.warning("ws_candle_parse_error", data=data)
            return

        # Use close as last, compute mid from high/low as approximation
        # Real mid comes from allMids subscription
        mid = self._mid_prices.get(symbol, close)

        tick = Tick(
            timestamp_ms=close_time,
            symbol=symbol,
            bid=mid - 0.005,  # Approximation, replaced by allMids
            ask=mid + 0.005,
            mid=mid,
            last=close,
            volume=volume,
        )

        # Emit tick callback
        if self._on_tick:
            await self._on_tick(tick)

        # Detect new candle close
        prev_close_time = self._last_close_time.get(symbol, 0)
        if close_time > prev_close_time:
            self._last_close_time[symbol] = close_time

            if self._on_candle and prev_close_time > 0:
                await self._on_candle(
                    symbol=symbol,
                    timestamp_ms=close_time,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    mid=mid,
                )

        log.debug(
            "ws_candle_received",
            symbol=symbol,
            close=close,
            mid=mid,
            zscore=None,
        )

    def _handle_all_mids(self, data: dict[str, Any]) -> None:
        """Update mid prices from allMids channel."""
        mids = data.get("mids", {})
        for raw_symbol, price_str in mids.items():
            try:
                symbol = raw_symbol.split(":")[-1] if ":" in raw_symbol else raw_symbol
                self._mid_prices[symbol] = float(price_str)
            except (ValueError, TypeError):
                continue

    async def _stale_detector(self) -> None:
        """Background task to detect stale connections and force reconnect."""
        timeout = self._config.stale_timeout_sec
        stale_since = 0.0
        while self._running:
            try:
                await asyncio.sleep(5.0)  # Check every 5s

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
                            "ws_stale_detected",
                            elapsed_sec=round(elapsed, 1),
                            timeout=timeout,
                        )

                    # Force reconnection after 2x timeout with no recovery
                    stale_elapsed = time.time() - stale_since
                    if stale_elapsed > timeout * 2:
                        log.error("ws_stale_too_long", stale_sec=round(stale_elapsed, 1))
                        if self._ws:
                            try:
                                await self._ws.close()
                            except Exception:
                                pass
                        stale_since = 0.0

            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("stale_detector_error")
                await asyncio.sleep(10.0)
