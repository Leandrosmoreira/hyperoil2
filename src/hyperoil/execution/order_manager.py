"""Order manager — lifecycle tracking for pair trade orders.

Handles the atomic placement of two-leg orders (left + right),
timeout detection, and status tracking.
"""

from __future__ import annotations

import asyncio
import uuid

from hyperoil.config import ExecutionConfig, SymbolsConfig
from hyperoil.execution.client import HyperliquidClient, OrderResult
from hyperoil.observability.logger import get_logger
from hyperoil.types import (
    Direction,
    OrderRequest,
    OrderSide,
    OrderState,
    OrderStatus,
    now_ms,
)

log = get_logger(__name__)


class OrderManager:
    """Manages the lifecycle of pair trade orders.

    Sends both legs, tracks fill status, and detects timeouts
    for hedge emergency escalation.
    """

    def __init__(
        self,
        client: HyperliquidClient,
        config: ExecutionConfig,
        symbols: SymbolsConfig,
    ) -> None:
        self._client = client
        self._config = config
        self._symbols = symbols
        self._orders: dict[str, OrderState] = {}
        self._pair_groups: dict[str, list[str]] = {}  # group_id → [order_ids]

    @property
    def active_orders(self) -> dict[str, OrderState]:
        return {
            oid: o for oid, o in self._orders.items()
            if o.status in (OrderStatus.PENDING, OrderStatus.SENT, OrderStatus.PARTIAL)
        }

    def get_order(self, order_id: str) -> OrderState | None:
        return self._orders.get(order_id)

    def get_pair_group(self, group_id: str) -> list[OrderState]:
        """Get all orders in a pair group."""
        oids = self._pair_groups.get(group_id, [])
        return [self._orders[oid] for oid in oids if oid in self._orders]

    async def send_pair_entry(
        self,
        cycle_id: str,
        direction: Direction,
        size_left: float,
        size_right: float,
        level: int,
    ) -> tuple[str, OrderState, OrderState]:
        """Send a pair of entry orders (both legs).

        Returns (group_id, left_order_state, right_order_state).
        """
        group_id = f"grp-{uuid.uuid4().hex[:8]}"
        ts = now_ms()

        # Determine sides based on direction
        if direction == Direction.LONG_SPREAD:
            side_left = OrderSide.BUY    # long CL
            side_right = OrderSide.SELL   # short BRENT
        else:
            side_left = OrderSide.SELL    # short CL
            side_right = OrderSide.BUY    # long BRENT

        left_req = OrderRequest(
            order_id=f"ord-{uuid.uuid4().hex[:8]}",
            cycle_id=cycle_id,
            symbol=self._symbols.left,
            side=side_left,
            qty=size_left,
            price=None,
            level=level,
            leg="left",
        )
        right_req = OrderRequest(
            order_id=f"ord-{uuid.uuid4().hex[:8]}",
            cycle_id=cycle_id,
            symbol=self._symbols.right,
            side=side_right,
            qty=size_right,
            price=None,
            level=level,
            leg="right",
        )

        left_state = self._create_order_state(left_req, ts)
        right_state = self._create_order_state(right_req, ts)

        self._orders[left_req.order_id] = left_state
        self._orders[right_req.order_id] = right_state
        self._pair_groups[group_id] = [left_req.order_id, right_req.order_id]

        # Send both legs concurrently
        left_result, right_result = await asyncio.gather(
            self._send_order(left_req, left_state),
            self._send_order(right_req, right_state),
        )

        log.info(
            "pair_entry_sent",
            group_id=group_id,
            cycle_id=cycle_id,
            direction=direction.value,
            level=level,
            left_ok=left_result.success,
            right_ok=right_result.success,
        )

        return group_id, left_state, right_state

    async def send_pair_exit(
        self,
        cycle_id: str,
        direction: Direction,
        size_left: float,
        size_right: float,
    ) -> tuple[str, OrderState, OrderState]:
        """Send a pair of exit orders (close both legs).

        Returns (group_id, left_order_state, right_order_state).
        """
        group_id = f"grp-{uuid.uuid4().hex[:8]}"
        ts = now_ms()

        # Exit is opposite of entry
        if direction == Direction.LONG_SPREAD:
            side_left = OrderSide.SELL    # close long CL
            side_right = OrderSide.BUY    # close short BRENT
        else:
            side_left = OrderSide.BUY     # close short CL
            side_right = OrderSide.SELL   # close long BRENT

        left_req = OrderRequest(
            order_id=f"ord-{uuid.uuid4().hex[:8]}",
            cycle_id=cycle_id,
            symbol=self._symbols.left,
            side=side_left,
            qty=size_left,
            price=None,
            level=0,
            leg="left",
        )
        right_req = OrderRequest(
            order_id=f"ord-{uuid.uuid4().hex[:8]}",
            cycle_id=cycle_id,
            symbol=self._symbols.right,
            side=side_right,
            qty=size_right,
            price=None,
            level=0,
            leg="right",
        )

        left_state = self._create_order_state(left_req, ts)
        right_state = self._create_order_state(right_req, ts)

        self._orders[left_req.order_id] = left_state
        self._orders[right_req.order_id] = right_state
        self._pair_groups[group_id] = [left_req.order_id, right_req.order_id]

        left_result, right_result = await asyncio.gather(
            self._send_order(left_req, left_state),
            self._send_order(right_req, right_state),
        )

        log.info(
            "pair_exit_sent",
            group_id=group_id,
            cycle_id=cycle_id,
            left_ok=left_result.success,
            right_ok=right_result.success,
        )

        return group_id, left_state, right_state

    def check_pair_fill_status(self, group_id: str) -> tuple[bool, bool, bool]:
        """Check fill status for a pair group.

        Returns (both_filled, left_filled, right_filled).
        """
        orders = self.get_pair_group(group_id)
        if len(orders) != 2:
            return False, False, False

        left_filled = orders[0].status == OrderStatus.FILLED
        right_filled = orders[1].status == OrderStatus.FILLED
        return left_filled and right_filled, left_filled, right_filled

    def check_pair_timeout(self, group_id: str) -> tuple[bool, bool]:
        """Check if either leg of a pair has timed out.

        Returns (left_timed_out, right_timed_out).
        """
        orders = self.get_pair_group(group_id)
        if len(orders) != 2:
            return False, False

        timeout_ms = int(self._config.fill_timeout_sec * 1000)
        ts = now_ms()

        left_timeout = (
            orders[0].status in (OrderStatus.SENT, OrderStatus.PARTIAL)
            and ts - orders[0].created_at_ms > timeout_ms
        )
        right_timeout = (
            orders[1].status in (OrderStatus.SENT, OrderStatus.PARTIAL)
            and ts - orders[1].created_at_ms > timeout_ms
        )

        return left_timeout, right_timeout

    def mark_filled(
        self,
        order_id: str,
        qty_filled: float,
        avg_price: float,
        fees: float = 0.0,
        exchange_oid: int | None = None,
    ) -> None:
        """Mark an order as filled with execution details."""
        order = self._orders.get(order_id)
        if not order:
            return

        order.qty_filled = qty_filled
        order.avg_fill_price = avg_price
        order.fees = fees
        order.status = OrderStatus.FILLED
        order.updated_at_ms = now_ms()
        if exchange_oid is not None:
            order.exchange_order_id = str(exchange_oid)

        log.info(
            "order_filled",
            order_id=order_id,
            symbol=order.symbol,
            qty=qty_filled,
            price=avg_price,
            fees=fees,
        )

    def mark_failed(self, order_id: str, error: str) -> None:
        """Mark an order as failed."""
        order = self._orders.get(order_id)
        if not order:
            return

        order.status = OrderStatus.FAILED
        order.error = error
        order.updated_at_ms = now_ms()

        log.warning("order_failed", order_id=order_id, error=error)

    def cleanup_completed(self, max_age_ms: int = 300_000) -> int:
        """Remove old completed/failed orders from tracking.

        Returns count of cleaned up orders.
        """
        cutoff = now_ms() - max_age_ms
        to_remove = [
            oid for oid, o in self._orders.items()
            if o.status in (OrderStatus.FILLED, OrderStatus.FAILED, OrderStatus.CANCELLED)
            and o.updated_at_ms < cutoff
        ]
        for oid in to_remove:
            del self._orders[oid]

        # Clean up pair groups referencing removed orders
        empty_groups = [
            gid for gid, oids in self._pair_groups.items()
            if all(oid not in self._orders for oid in oids)
        ]
        for gid in empty_groups:
            del self._pair_groups[gid]

        return len(to_remove)

    # --- Private helpers ---

    @staticmethod
    def _create_order_state(req: OrderRequest, ts: int) -> OrderState:
        return OrderState(
            order_id=req.order_id,
            cycle_id=req.cycle_id,
            symbol=req.symbol,
            side=req.side,
            qty_requested=req.qty,
            status=OrderStatus.PENDING,
            created_at_ms=ts,
            updated_at_ms=ts,
        )

    async def _send_order(self, req: OrderRequest, state: OrderState) -> OrderResult:
        """Send a single order and update its state."""
        state.status = OrderStatus.SENT
        state.updated_at_ms = now_ms()

        result = await self._client.place_market_order(
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            cloid=req.order_id,
        )

        if result.success:
            if result.exchange_oid is not None:
                state.exchange_order_id = str(result.exchange_oid)

            if result.status == "filled":
                state.status = OrderStatus.FILLED
                state.qty_filled = req.qty
            elif result.status == "resting":
                state.status = OrderStatus.SENT
            else:
                state.status = OrderStatus.SENT
        else:
            state.status = OrderStatus.FAILED
            state.error = result.error

        state.updated_at_ms = now_ms()
        return result
