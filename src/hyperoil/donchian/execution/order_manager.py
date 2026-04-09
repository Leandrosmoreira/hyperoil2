"""Single-asset order manager for the Donchian strategy.

The Donchian portfolio operates over 25 INDEPENDENT positions, one per
asset. Unlike the pair-trading bot (where two legs must be hedged
atomically), each Donchian decision is a stand-alone single-asset action.
This module wraps a single ``HyperliquidClient`` and gives the orchestrator
a tiny, action-oriented API:

    await mgr.execute_enter(...)
    await mgr.execute_exit(...)
    await mgr.execute_resize(...)

Project rule: every entry, exit, increase and decrease MUST be maker
post-only by default. Only when ``order_policy.emergency_exit_order_type``
is "market" AND the decision reason indicates a trailing-stop hit do we
fall back to a taker market order — and even then we log it loudly so it
shows up in the JSONL ledger.

Sizing helper: HL HIP-3 perps require quantities rounded to ``szDecimals``.
The order manager rounds DOWN (never inflate the order) using the per-asset
``sz_decimals`` value loaded from the Sprint 0 ticker mapping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from hyperoil.donchian.config import OrderPolicyConfig
from hyperoil.execution.client import HyperliquidClient, OrderResult
from hyperoil.observability.logger import get_logger
from hyperoil.types import OrderSide

log = get_logger(__name__)


@dataclass(frozen=True)
class ExecutionOutcome:
    """Result of executing one Donchian decision against the exchange."""
    success: bool
    symbol: str
    side: str
    qty: float
    price: float
    order_type: str           # "limit_maker" | "market"
    order_id: str | None
    exchange_oid: int | None
    error: str | None
    raw: dict[str, Any] | None = None


def round_down_qty(notional_usd: float, price: float, sz_decimals: int) -> float:
    """Convert a USD notional to a base-asset qty, rounded DOWN to szDecimals.

    Rounding DOWN guarantees we never exceed the requested risk budget. The
    caller is expected to handle the case where rounded qty == 0 (notional
    too small to satisfy the lot-size constraint).
    """
    if price <= 0 or notional_usd <= 0:
        return 0.0
    raw = notional_usd / price
    factor = 10 ** sz_decimals
    return math.floor(raw * factor) / factor


class SingleOrderManager:
    """Tiny façade over HyperliquidClient for single-asset Donchian actions.

    Stateless: every call is a one-shot order. Live order tracking
    (resting state, cancels, partial fills) belongs in a future Sprint 7
    follow-up; for E1 we send aggressive post-only orders and trust the
    exchange's TIF=Alo to keep us as makers.
    """

    def __init__(
        self,
        client: HyperliquidClient,
        policy: OrderPolicyConfig,
        sz_decimals: dict[str, int] | None = None,
    ) -> None:
        self._client = client
        self._policy = policy
        # Map keyed by *internal* HL ticker (e.g. "BTC", not "hyna:BTC") so
        # the same map works for both xyz: and hyna: prefixes that share a
        # ticker name.
        self._sz_decimals = sz_decimals or {}

    # ------------------------------------------------------------------
    # Public API — one method per Donchian action
    # ------------------------------------------------------------------
    async def execute_enter(
        self,
        symbol: str,
        notional_usd: float,
        price: float,
        cloid: str | None = None,
    ) -> ExecutionOutcome:
        """Open a new long position. ALWAYS post-only by project rule."""
        return await self._post_only(symbol, OrderSide.BUY, notional_usd, price, cloid, "enter")

    async def execute_increase(
        self,
        symbol: str,
        delta_notional_usd: float,
        price: float,
        cloid: str | None = None,
    ) -> ExecutionOutcome:
        """Add to an existing long. Post-only."""
        return await self._post_only(
            symbol, OrderSide.BUY, delta_notional_usd, price, cloid, "increase",
        )

    async def execute_decrease(
        self,
        symbol: str,
        delta_notional_usd: float,
        price: float,
        cloid: str | None = None,
    ) -> ExecutionOutcome:
        """Trim an existing long. Post-only."""
        return await self._post_only(
            symbol, OrderSide.SELL, delta_notional_usd, price, cloid, "decrease",
        )

    async def execute_exit(
        self,
        symbol: str,
        notional_usd: float,
        price: float,
        reason: str,
        cloid: str | None = None,
    ) -> ExecutionOutcome:
        """Fully close a long position.

        Post-only by default. Only ``stop_hit`` exits route through the
        emergency taker path (and only if the policy enables it) — every
        other exit reason waits patiently as a maker.
        """
        if reason == "stop_hit" and self._policy.emergency_exit_order_type == "market":
            return await self._market(symbol, OrderSide.SELL, notional_usd, price, cloid, "exit_stop")
        return await self._post_only(symbol, OrderSide.SELL, notional_usd, price, cloid, "exit")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _qty_or_zero(self, symbol: str, notional_usd: float, price: float) -> float:
        """Convert notional → qty using the asset's sz_decimals. Returns 0
        if the rounded qty would be zero (sub-lot order)."""
        sd = self._sz_decimals.get(self._ticker(symbol), 0)
        return round_down_qty(notional_usd, price, sd)

    @staticmethod
    def _ticker(dex_symbol: str) -> str:
        return dex_symbol.split(":", 1)[-1]

    async def _post_only(
        self,
        symbol: str,
        side: OrderSide,
        notional_usd: float,
        price: float,
        cloid: str | None,
        action: str,
    ) -> ExecutionOutcome:
        qty = self._qty_or_zero(symbol, notional_usd, price)
        if qty <= 0:
            log.warning("order_qty_zero", symbol=symbol, action=action,
                        notional=notional_usd, price=price)
            return ExecutionOutcome(
                success=False, symbol=symbol, side=side.value, qty=0.0,
                price=price, order_type="limit_maker", order_id=cloid,
                exchange_oid=None, error="qty_rounded_to_zero",
            )

        # Place at the inside book by default. Sprint 7 will add the
        # retry-with-offset loop driven by post_only_retry_offset_bps.
        result = await self._client.place_limit_post_only(
            symbol=self._ticker(symbol), side=side, qty=qty, price=price, cloid=cloid,
        )
        return self._wrap(result, symbol, side, qty, price, "limit_maker")

    async def _market(
        self,
        symbol: str,
        side: OrderSide,
        notional_usd: float,
        price: float,
        cloid: str | None,
        action: str,
    ) -> ExecutionOutcome:
        qty = self._qty_or_zero(symbol, notional_usd, price)
        if qty <= 0:
            log.warning("emergency_qty_zero", symbol=symbol, action=action,
                        notional=notional_usd)
            return ExecutionOutcome(
                success=False, symbol=symbol, side=side.value, qty=0.0,
                price=price, order_type="market", order_id=cloid,
                exchange_oid=None, error="qty_rounded_to_zero",
            )

        log.warning("emergency_taker_exit", symbol=symbol, qty=qty, price=price)
        result = await self._client.place_market_order(
            symbol=self._ticker(symbol), side=side, qty=qty, cloid=cloid,
        )
        return self._wrap(result, symbol, side, qty, price, "market")

    @staticmethod
    def _wrap(
        result: OrderResult, symbol: str, side: OrderSide,
        qty: float, price: float, order_type: str,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            success=result.success,
            symbol=symbol,
            side=side.value,
            qty=qty,
            price=price,
            order_type=order_type,
            order_id=result.order_id,
            exchange_oid=result.exchange_oid,
            error=result.error,
            raw=result.raw,
        )
