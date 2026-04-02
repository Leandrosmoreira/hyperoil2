"""Async wrapper around the synchronous Hyperliquid Python SDK.

The SDK is synchronous — all calls are dispatched via asyncio.to_thread()
to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from hyperoil.config import ExecutionConfig
from hyperoil.observability.logger import get_logger
from hyperoil.types import OrderSide, now_ms

log = get_logger(__name__)


@dataclass(frozen=True)
class FillInfo:
    """Parsed fill from exchange response."""
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    fee: float
    timestamp_ms: int


@dataclass(frozen=True)
class OrderResult:
    """Result of placing an order on the exchange."""
    success: bool
    order_id: str | None = None
    exchange_oid: int | None = None
    status: str = ""
    error: str | None = None
    raw: dict[str, Any] | None = None


class HyperliquidClient:
    """Async wrapper for the Hyperliquid SDK.

    In paper mode, orders are simulated locally.
    In live mode, orders are routed to the exchange via asyncio.to_thread().
    """

    def __init__(
        self,
        config: ExecutionConfig,
        private_key: str = "",
        wallet_address: str = "",
        api_url: str = "https://api.hyperliquid.xyz",
    ) -> None:
        self._config = config
        self._private_key = private_key
        self._wallet_address = wallet_address
        self._api_url = api_url
        self._exchange: Any = None
        self._info: Any = None
        self._connected = False
        self._paper_oid_counter = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_paper(self) -> bool:
        return self._config.mode == "paper"

    async def connect(self) -> None:
        """Initialize SDK connection. In paper mode, this is a no-op."""
        if self.is_paper:
            log.info("execution_client_paper_mode")
            self._connected = True
            return

        if not self._private_key:
            raise ValueError("HYPERLIQUID_PRIVATE_KEY required for live mode")

        def _init_sdk() -> None:
            from eth_account import Account  # type: ignore[import-untyped]
            from hyperliquid.exchange import Exchange  # type: ignore[import-untyped]
            from hyperliquid.info import Info  # type: ignore[import-untyped]

            wallet = Account.from_key(self._private_key)
            self._exchange = Exchange(
                wallet=wallet,
                base_url=self._api_url,
            )
            self._info = Info(base_url=self._api_url)

        await asyncio.to_thread(_init_sdk)
        self._connected = True
        log.info("execution_client_connected", mode="live")

    async def disconnect(self) -> None:
        """Clean up SDK resources."""
        self._exchange = None
        self._info = None
        self._connected = False
        log.info("execution_client_disconnected")

    async def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        cloid: str | None = None,
    ) -> OrderResult:
        """Place a market order (IOC limit at aggressive price).

        Args:
            symbol: Hyperliquid symbol (e.g. "CL")
            side: BUY or SELL
            qty: Quantity in base units
            cloid: Optional client order ID
        """
        if not self._connected:
            return OrderResult(success=False, error="client_not_connected")

        if self.is_paper:
            return self._paper_market_order(symbol, side, qty, cloid)

        return await self._live_market_order(symbol, side, qty, cloid)

    async def cancel_order(self, symbol: str, exchange_oid: int) -> bool:
        """Cancel an open order by exchange OID."""
        if not self._connected:
            return False

        if self.is_paper:
            log.info("paper_cancel", symbol=symbol, oid=exchange_oid)
            return True

        def _cancel() -> dict:
            return self._exchange.cancel(symbol, exchange_oid)

        try:
            result = await asyncio.to_thread(_cancel)
            success = result.get("status", "") == "ok"
            log.info(
                "order_cancelled",
                symbol=symbol,
                oid=exchange_oid,
                success=success,
            )
            return success
        except Exception as exc:
            log.error("cancel_failed", symbol=symbol, oid=exchange_oid, error=str(exc))
            return False

    async def get_open_orders(self, wallet: str | None = None) -> list[dict]:
        """Get open orders for the wallet."""
        if not self._connected or self.is_paper:
            return []

        addr = wallet or self._wallet_address

        def _fetch() -> list[dict]:
            return self._info.open_orders(addr)

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            log.error("fetch_open_orders_failed", error=str(exc))
            return []

    async def get_user_state(self, wallet: str | None = None) -> dict | None:
        """Get user state (positions, balances) from exchange."""
        if not self._connected or self.is_paper:
            return None

        addr = wallet or self._wallet_address

        def _fetch() -> dict:
            return self._info.user_state(addr)

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            log.error("fetch_user_state_failed", error=str(exc))
            return None

    async def get_user_fills(
        self, wallet: str | None = None, start_time: int | None = None,
    ) -> list[dict]:
        """Get recent fills for the wallet."""
        if not self._connected or self.is_paper:
            return []

        addr = wallet or self._wallet_address

        def _fetch() -> list[dict]:
            return self._info.user_fills(addr)

        try:
            fills = await asyncio.to_thread(_fetch)
            if start_time:
                fills = [f for f in fills if f.get("time", 0) >= start_time]
            return fills
        except Exception as exc:
            log.error("fetch_user_fills_failed", error=str(exc))
            return []

    # --- Private helpers ---

    def _paper_market_order(
        self, symbol: str, side: OrderSide, qty: float, cloid: str | None,
    ) -> OrderResult:
        """Simulate a market order fill in paper mode."""
        self._paper_oid_counter += 1
        oid = self._paper_oid_counter
        order_id = cloid or f"paper-{oid}"

        log.info(
            "paper_order_filled",
            symbol=symbol,
            side=side.value,
            qty=qty,
            order_id=order_id,
        )

        return OrderResult(
            success=True,
            order_id=order_id,
            exchange_oid=oid,
            status="filled",
        )

    async def _live_market_order(
        self, symbol: str, side: OrderSide, qty: float, cloid: str | None,
    ) -> OrderResult:
        """Execute a market order via the SDK."""
        is_buy = side == OrderSide.BUY

        def _place() -> dict:
            if is_buy:
                return self._exchange.market_open(
                    name=symbol,
                    is_buy=True,
                    sz=qty,
                    cloid=cloid,
                )
            else:
                return self._exchange.market_open(
                    name=symbol,
                    is_buy=False,
                    sz=qty,
                    cloid=cloid,
                )

        try:
            result = await asyncio.to_thread(_place)
            return self._parse_order_result(result, cloid)
        except Exception as exc:
            log.error(
                "live_order_failed",
                symbol=symbol,
                side=side.value,
                qty=qty,
                error=str(exc),
            )
            return OrderResult(
                success=False,
                order_id=cloid,
                error=str(exc),
            )

    @staticmethod
    def _parse_order_result(result: dict, cloid: str | None) -> OrderResult:
        """Parse SDK order response into OrderResult."""
        status = result.get("status", "")
        if status == "ok":
            response = result.get("response", {})
            data = response.get("data", {})
            statuses = data.get("statuses", [])
            if statuses:
                first = statuses[0]
                if "filled" in first:
                    filled = first["filled"]
                    return OrderResult(
                        success=True,
                        order_id=cloid,
                        exchange_oid=filled.get("oid"),
                        status="filled",
                        raw=result,
                    )
                elif "resting" in first:
                    resting = first["resting"]
                    return OrderResult(
                        success=True,
                        order_id=cloid,
                        exchange_oid=resting.get("oid"),
                        status="resting",
                        raw=result,
                    )
                elif "error" in first:
                    return OrderResult(
                        success=False,
                        order_id=cloid,
                        error=first["error"],
                        raw=result,
                    )

            return OrderResult(
                success=True,
                order_id=cloid,
                status="ok",
                raw=result,
            )

        return OrderResult(
            success=False,
            order_id=cloid,
            error=result.get("error", f"unknown status: {status}"),
            raw=result,
        )
