"""Fill tracker — tracks actual fills, fees, and slippage per order.

Computes real execution quality metrics by comparing fill prices
against mid prices at time of order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyperoil.config import BacktestConfig
from hyperoil.observability.logger import get_logger
from hyperoil.types import OrderSide, now_ms

log = get_logger(__name__)


@dataclass
class Fill:
    """A single fill event."""
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    fee: float
    timestamp_ms: int
    mid_price_at_order: float = 0.0

    @property
    def slippage_bps(self) -> float:
        """Slippage in basis points relative to mid price at order time."""
        if self.mid_price_at_order <= 0:
            return 0.0
        if self.side == OrderSide.BUY:
            # Buying higher than mid = negative slippage
            return (self.price - self.mid_price_at_order) / self.mid_price_at_order * 10_000
        else:
            # Selling lower than mid = negative slippage
            return (self.mid_price_at_order - self.price) / self.mid_price_at_order * 10_000


@dataclass
class CycleFillSummary:
    """Aggregated fill metrics for a complete cycle."""
    cycle_id: str
    total_fills: int = 0
    total_fees: float = 0.0
    avg_slippage_bps: float = 0.0
    total_notional: float = 0.0
    entry_fills: list[Fill] = field(default_factory=list)
    exit_fills: list[Fill] = field(default_factory=list)


class FillTracker:
    """Tracks fills and computes execution quality metrics.

    In paper mode, simulates fills with configurable fee/slippage models.
    In live mode, records actual fill data from the exchange.
    """

    def __init__(self, backtest_config: BacktestConfig | None = None) -> None:
        self._fills: dict[str, list[Fill]] = {}  # order_id → fills
        self._cycle_fills: dict[str, list[Fill]] = {}  # cycle_id → fills
        self._mid_prices: dict[str, float] = {}  # order_id → mid at order time
        self._bt_config = backtest_config

    def register_mid_price(self, order_id: str, mid_price: float) -> None:
        """Register the mid price at order submission time for slippage calculation."""
        self._mid_prices[order_id] = mid_price

    def record_fill(
        self,
        order_id: str,
        cycle_id: str,
        symbol: str,
        side: OrderSide,
        qty: float,
        price: float,
        fee: float,
        timestamp_ms: int | None = None,
    ) -> Fill:
        """Record a fill from the exchange."""
        ts = timestamp_ms or now_ms()
        mid = self._mid_prices.get(order_id, 0.0)

        fill = Fill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            fee=fee,
            timestamp_ms=ts,
            mid_price_at_order=mid,
        )

        self._fills.setdefault(order_id, []).append(fill)
        self._cycle_fills.setdefault(cycle_id, []).append(fill)

        log.info(
            "fill_recorded",
            order_id=order_id,
            cycle_id=cycle_id,
            symbol=symbol,
            side=side.value,
            qty=qty,
            price=price,
            fee=fee,
            slippage_bps=round(fill.slippage_bps, 2),
        )

        return fill

    def simulate_fill(
        self,
        order_id: str,
        cycle_id: str,
        symbol: str,
        side: OrderSide,
        qty: float,
        mid_price: float,
    ) -> Fill:
        """Simulate a fill in paper/backtest mode with fee and slippage models."""
        cfg = self._bt_config or BacktestConfig()

        # Apply slippage model
        slippage_bps = cfg.slippage_fixed_bps + cfg.slippage_proportional_bps
        slippage_mult = slippage_bps / 10_000

        if side == OrderSide.BUY:
            fill_price = mid_price * (1 + slippage_mult)
        else:
            fill_price = mid_price * (1 - slippage_mult)

        # Compute fee
        fee_bps = cfg.fee_taker_bps  # market orders are taker
        fee = qty * fill_price * fee_bps / 10_000

        self.register_mid_price(order_id, mid_price)

        return self.record_fill(
            order_id=order_id,
            cycle_id=cycle_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=round(fill_price, 6),
            fee=round(fee, 6),
        )

    def get_order_fills(self, order_id: str) -> list[Fill]:
        return self._fills.get(order_id, [])

    def get_cycle_fills(self, cycle_id: str) -> list[Fill]:
        return self._cycle_fills.get(cycle_id, [])

    def get_cycle_summary(self, cycle_id: str) -> CycleFillSummary:
        """Compute aggregated fill metrics for a cycle."""
        fills = self.get_cycle_fills(cycle_id)

        total_fees = sum(f.fee for f in fills)
        total_notional = sum(f.qty * f.price for f in fills)

        slippages = [f.slippage_bps for f in fills if f.mid_price_at_order > 0]
        avg_slippage = sum(slippages) / len(slippages) if slippages else 0.0

        return CycleFillSummary(
            cycle_id=cycle_id,
            total_fills=len(fills),
            total_fees=round(total_fees, 6),
            avg_slippage_bps=round(avg_slippage, 2),
            total_notional=round(total_notional, 2),
        )

    def get_total_fees(self, cycle_id: str) -> float:
        """Total fees paid in a cycle."""
        return sum(f.fee for f in self.get_cycle_fills(cycle_id))

    def cleanup_cycle(self, cycle_id: str) -> None:
        """Remove tracked fills for a completed cycle (after persistence)."""
        if cycle_id in self._cycle_fills:
            # Clean up per-order tracking for fills in this cycle
            for fill in self._cycle_fills[cycle_id]:
                self._fills.pop(fill.order_id, None)
                self._mid_prices.pop(fill.order_id, None)
            del self._cycle_fills[cycle_id]
