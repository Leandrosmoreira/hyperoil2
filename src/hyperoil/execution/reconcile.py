"""Reconciliation — syncs local order/position state with the exchange.

Runs periodically to detect drift between local tracking and
the exchange's authoritative state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperoil.execution.client import HyperliquidClient
from hyperoil.execution.order_manager import OrderManager
from hyperoil.observability.logger import get_logger
from hyperoil.types import OrderStatus, now_ms

log = get_logger(__name__)


@dataclass(frozen=True)
class ReconcileResult:
    """Result of a reconciliation check."""
    timestamp_ms: int
    positions_match: bool
    orders_match: bool
    stale_orders_cancelled: int = 0
    unknown_positions: list[str] = field(default_factory=list)
    drift_details: dict[str, str] = field(default_factory=dict)


class Reconciler:
    """Periodically reconciles local state with exchange state.

    Detects:
    - Orders we think are open but exchange says are filled/cancelled
    - Positions on exchange that we don't track locally
    - Stale orders that should be cancelled
    """

    def __init__(
        self,
        client: HyperliquidClient,
        order_manager: OrderManager,
    ) -> None:
        self._client = client
        self._order_mgr = order_manager
        self._last_reconcile_ms: int = 0
        self._consecutive_failures: int = 0

    @property
    def last_reconcile_ms(self) -> int:
        return self._last_reconcile_ms

    async def reconcile(self, tracked_symbols: set[str] | None = None) -> ReconcileResult:
        """Run a full reconciliation cycle.

        Args:
            tracked_symbols: Symbols we expect to have positions in.
                             If None, skip position reconciliation.
        """
        ts = now_ms()

        if self._client.is_paper:
            self._last_reconcile_ms = ts
            return ReconcileResult(
                timestamp_ms=ts,
                positions_match=True,
                orders_match=True,
            )

        try:
            orders_match, stale_cancelled = await self._reconcile_orders()
            positions_match, unknown = await self._reconcile_positions(tracked_symbols or set())

            self._last_reconcile_ms = ts
            self._consecutive_failures = 0

            result = ReconcileResult(
                timestamp_ms=ts,
                positions_match=positions_match,
                orders_match=orders_match,
                stale_orders_cancelled=stale_cancelled,
                unknown_positions=unknown,
            )

            if not orders_match or not positions_match:
                log.warning(
                    "reconcile_drift_detected",
                    orders_match=orders_match,
                    positions_match=positions_match,
                    stale_cancelled=stale_cancelled,
                    unknown_positions=unknown,
                )

            return result

        except Exception as exc:
            self._consecutive_failures += 1
            log.error(
                "reconcile_failed",
                error=str(exc),
                consecutive_failures=self._consecutive_failures,
            )
            return ReconcileResult(
                timestamp_ms=ts,
                positions_match=False,
                orders_match=False,
                drift_details={"error": str(exc)},
            )

    async def _reconcile_orders(self) -> tuple[bool, int]:
        """Compare local order state with exchange open orders.

        Returns (orders_match, stale_orders_cancelled).
        """
        exchange_orders = await self._client.get_open_orders()
        exchange_oids = {str(o.get("oid", "")) for o in exchange_orders}

        local_active = self._order_mgr.active_orders
        all_match = True
        stale_cancelled = 0

        for oid, order in local_active.items():
            if order.exchange_order_id and order.exchange_order_id not in exchange_oids:
                # We think it's open, but exchange says it's not
                if order.status == OrderStatus.SENT:
                    log.info(
                        "reconcile_order_gone",
                        order_id=oid,
                        exchange_oid=order.exchange_order_id,
                    )
                    # Assume filled — fill tracker will pick up actual fill
                    self._order_mgr.mark_filled(
                        oid,
                        qty_filled=order.qty_requested,
                        avg_price=0.0,  # will be updated from fills
                    )
                    all_match = False

        return all_match, stale_cancelled

    async def _reconcile_positions(
        self, tracked_symbols: set[str],
    ) -> tuple[bool, list[str]]:
        """Check for unexpected positions on the exchange.

        Returns (positions_match, list of unknown symbols with positions).
        """
        if not tracked_symbols:
            return True, []

        user_state = await self._client.get_user_state()
        if not user_state:
            return True, []

        unknown: list[str] = []
        positions = user_state.get("assetPositions", [])

        for pos in positions:
            position = pos.get("position", {})
            coin = position.get("coin", "")
            szi = float(position.get("szi", "0"))

            if abs(szi) > 0 and coin not in tracked_symbols:
                unknown.append(coin)
                log.warning(
                    "reconcile_unknown_position",
                    symbol=coin,
                    size=szi,
                )

        return len(unknown) == 0, unknown

    async def fetch_recent_fills(self, start_time_ms: int) -> list[dict]:
        """Fetch fills from exchange since a given timestamp."""
        return await self._client.get_user_fills(start_time=start_time_ms)
