"""Hedge emergency — protects against orphaned legs.

If one leg of a pair fills and the other doesn't within the timeout,
this module fires a market order to close the exposed leg immediately.
"""

from __future__ import annotations

from dataclasses import dataclass

from hyperoil.config import ExecutionConfig
from hyperoil.execution.client import HyperliquidClient
from hyperoil.execution.order_manager import OrderManager
from hyperoil.observability.logger import get_logger
from hyperoil.storage.jsonl_writer import JsonlWriter
from hyperoil.types import OrderSide, OrderStatus, now_ms

log = get_logger(__name__)


@dataclass(frozen=True)
class HedgeAction:
    """Record of an emergency hedge action taken."""
    timestamp_ms: int
    group_id: str
    filled_leg: str     # "left" or "right"
    unfilled_leg: str
    action: str          # "hedge_market" or "cancel_both" or "none"
    success: bool
    error: str | None = None


class HedgeEmergency:
    """Monitors pair order groups for orphaned legs and takes corrective action.

    Rules:
    - If one leg fills and the other times out → market order the unfilled leg
    - If both legs fail → cancel everything, log incident
    - Never leave a single-sided position without a hedge attempt
    """

    def __init__(
        self,
        client: HyperliquidClient,
        order_manager: OrderManager,
        config: ExecutionConfig,
        jsonl_writer: JsonlWriter | None = None,
    ) -> None:
        self._client = client
        self._order_mgr = order_manager
        self._config = config
        self._jsonl = jsonl_writer
        self._actions: list[HedgeAction] = []

    @property
    def actions(self) -> list[HedgeAction]:
        return list(self._actions)

    async def check_group(self, group_id: str) -> HedgeAction | None:
        """Check a pair group for orphaned legs and take action if needed.

        Returns a HedgeAction if corrective action was taken, None otherwise.
        """
        if not self._config.emergency_hedge:
            return None

        orders = self._order_mgr.get_pair_group(group_id)
        if len(orders) != 2:
            return None

        left, right = orders[0], orders[1]
        both_filled, left_filled, right_filled = self._order_mgr.check_pair_fill_status(group_id)

        if both_filled:
            return None  # All good

        # Check for timeout
        left_timeout, right_timeout = self._order_mgr.check_pair_timeout(group_id)

        # Both failed — cancel and log
        if left.status == OrderStatus.FAILED and right.status == OrderStatus.FAILED:
            action = HedgeAction(
                timestamp_ms=now_ms(),
                group_id=group_id,
                filled_leg="none",
                unfilled_leg="both",
                action="cancel_both",
                success=True,
            )
            self._actions.append(action)
            await self._log_incident(action)
            return action

        # One filled, other timed out or failed → hedge emergency
        if left_filled and (right_timeout or right.status == OrderStatus.FAILED):
            return await self._hedge_unfilled_leg(
                group_id=group_id,
                filled_leg="left",
                unfilled_order=right,
            )

        if right_filled and (left_timeout or left.status == OrderStatus.FAILED):
            return await self._hedge_unfilled_leg(
                group_id=group_id,
                filled_leg="right",
                unfilled_order=left,
            )

        # Both timed out but neither filled — cancel both
        if left_timeout and right_timeout and not left_filled and not right_filled:
            await self._cancel_order_safe(left)
            await self._cancel_order_safe(right)

            action = HedgeAction(
                timestamp_ms=now_ms(),
                group_id=group_id,
                filled_leg="none",
                unfilled_leg="both",
                action="cancel_both",
                success=True,
            )
            self._actions.append(action)
            await self._log_incident(action)
            return action

        return None

    async def _hedge_unfilled_leg(
        self,
        group_id: str,
        filled_leg: str,
        unfilled_order: object,
    ) -> HedgeAction:
        """Send emergency market order to hedge an orphaned leg."""
        from hyperoil.types import OrderState

        order: OrderState = unfilled_order  # type: ignore[assignment]

        log.warning(
            "hedge_emergency_triggered",
            group_id=group_id,
            filled_leg=filled_leg,
            unfilled_symbol=order.symbol,
            unfilled_side=order.side.value,
            unfilled_qty=order.qty_requested,
        )

        # Cancel the stuck order first (if it has an exchange OID)
        await self._cancel_order_safe(order)

        # Send market order for the unfilled leg
        result = await self._client.place_market_order(
            symbol=order.symbol,
            side=order.side,
            qty=order.qty_requested,
            cloid=f"hedge-{order.order_id}",
        )

        success = result.success
        error = result.error if not success else None

        if success:
            self._order_mgr.mark_filled(
                order.order_id,
                qty_filled=order.qty_requested,
                avg_price=0.0,  # will be updated from fills
            )
            log.info(
                "hedge_emergency_success",
                group_id=group_id,
                symbol=order.symbol,
            )
        else:
            self._order_mgr.mark_failed(order.order_id, error or "hedge_emergency_failed")
            log.error(
                "hedge_emergency_failed",
                group_id=group_id,
                symbol=order.symbol,
                error=error,
            )

        action = HedgeAction(
            timestamp_ms=now_ms(),
            group_id=group_id,
            filled_leg=filled_leg,
            unfilled_leg="right" if filled_leg == "left" else "left",
            action="hedge_market",
            success=success,
            error=error,
        )
        self._actions.append(action)
        await self._log_incident(action)
        return action

    async def _cancel_order_safe(self, order: object) -> None:
        """Attempt to cancel an order, ignoring failures."""
        from hyperoil.types import OrderState

        o: OrderState = order  # type: ignore[assignment]
        if o.exchange_order_id:
            try:
                await self._client.cancel_order(o.symbol, int(o.exchange_order_id))
            except Exception as exc:
                log.warning("cancel_in_hedge_failed", order_id=o.order_id, error=str(exc))

    async def _log_incident(self, action: HedgeAction) -> None:
        """Persist hedge emergency incident to JSONL audit trail."""
        if not self._jsonl:
            return

        await self._jsonl.write_incident(
            incident_type="hedge_emergency",
            details={
                "group_id": action.group_id,
                "filled_leg": action.filled_leg,
                "unfilled_leg": action.unfilled_leg,
                "action": action.action,
                "success": action.success,
                "error": action.error,
            },
        )
