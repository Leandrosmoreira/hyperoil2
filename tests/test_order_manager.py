"""Tests for the order manager."""

from __future__ import annotations

import pytest

from hyperoil.config import ExecutionConfig, SymbolsConfig
from hyperoil.execution.client import HyperliquidClient
from hyperoil.execution.order_manager import OrderManager
from hyperoil.types import Direction, OrderSide, OrderStatus


def _make_manager() -> OrderManager:
    config = ExecutionConfig(mode="paper", fill_timeout_sec=3.0)
    client = HyperliquidClient(config=config)
    symbols = SymbolsConfig(left="CL", right="BRENTOIL")
    return OrderManager(client=client, config=config, symbols=symbols)


async def _connected_manager() -> OrderManager:
    config = ExecutionConfig(mode="paper", fill_timeout_sec=3.0)
    client = HyperliquidClient(config=config)
    await client.connect()
    symbols = SymbolsConfig(left="CL", right="BRENTOIL")
    return OrderManager(client=client, config=config, symbols=symbols)


class TestOrderManager:
    @pytest.mark.asyncio
    async def test_send_pair_entry_long_spread(self) -> None:
        mgr = await _connected_manager()

        group_id, left, right = await mgr.send_pair_entry(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.46,
            size_right=1.38,
            level=1,
        )

        assert group_id.startswith("grp-")
        assert left.side == OrderSide.BUY       # long CL
        assert right.side == OrderSide.SELL      # short BRENT
        assert left.symbol == "CL"
        assert right.symbol == "BRENTOIL"
        # Paper mode fills immediately
        assert left.status == OrderStatus.FILLED
        assert right.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_send_pair_entry_short_spread(self) -> None:
        mgr = await _connected_manager()

        _, left, right = await mgr.send_pair_entry(
            cycle_id="cycle-002",
            direction=Direction.SHORT_SPREAD,
            size_left=1.46,
            size_right=1.38,
            level=1,
        )

        assert left.side == OrderSide.SELL       # short CL
        assert right.side == OrderSide.BUY       # long BRENT

    @pytest.mark.asyncio
    async def test_send_pair_exit_long_spread(self) -> None:
        mgr = await _connected_manager()

        _, left, right = await mgr.send_pair_exit(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.46,
            size_right=1.38,
        )

        assert left.side == OrderSide.SELL       # close long CL
        assert right.side == OrderSide.BUY       # close short BRENT

    @pytest.mark.asyncio
    async def test_send_pair_exit_short_spread(self) -> None:
        mgr = await _connected_manager()

        _, left, right = await mgr.send_pair_exit(
            cycle_id="cycle-002",
            direction=Direction.SHORT_SPREAD,
            size_left=1.46,
            size_right=1.38,
        )

        assert left.side == OrderSide.BUY        # close short CL
        assert right.side == OrderSide.SELL       # close long BRENT

    @pytest.mark.asyncio
    async def test_check_pair_fill_status(self) -> None:
        mgr = await _connected_manager()

        group_id, _, _ = await mgr.send_pair_entry(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        both, left, right = mgr.check_pair_fill_status(group_id)
        assert both  # paper mode fills immediately
        assert left
        assert right

    @pytest.mark.asyncio
    async def test_check_pair_fill_nonexistent_group(self) -> None:
        mgr = await _connected_manager()
        both, left, right = mgr.check_pair_fill_status("nonexistent")
        assert not both
        assert not left
        assert not right

    @pytest.mark.asyncio
    async def test_mark_filled(self) -> None:
        mgr = await _connected_manager()

        group_id, left, right = await mgr.send_pair_entry(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        # In paper mode these are already filled, but test mark_filled explicitly
        mgr.mark_filled(left.order_id, qty_filled=1.0, avg_price=68.50, fees=0.02)
        order = mgr.get_order(left.order_id)
        assert order is not None
        assert order.avg_fill_price == 68.50
        assert order.fees == 0.02

    @pytest.mark.asyncio
    async def test_mark_failed(self) -> None:
        mgr = await _connected_manager()

        group_id, left, _ = await mgr.send_pair_entry(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        mgr.mark_failed(left.order_id, "test_error")
        order = mgr.get_order(left.order_id)
        assert order is not None
        assert order.status == OrderStatus.FAILED
        assert order.error == "test_error"

    @pytest.mark.asyncio
    async def test_active_orders_excludes_filled(self) -> None:
        mgr = await _connected_manager()

        await mgr.send_pair_entry(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        # Paper mode fills immediately, so no active orders
        assert len(mgr.active_orders) == 0

    @pytest.mark.asyncio
    async def test_cleanup_completed(self) -> None:
        mgr = await _connected_manager()

        _, left, right = await mgr.send_pair_entry(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        # Backdate the updated_at_ms so they're old enough to clean
        left.updated_at_ms = 0
        right.updated_at_ms = 0
        removed = mgr.cleanup_completed(max_age_ms=1000)
        assert removed == 2

    @pytest.mark.asyncio
    async def test_get_pair_group(self) -> None:
        mgr = await _connected_manager()

        group_id, _, _ = await mgr.send_pair_entry(
            cycle_id="cycle-001",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        orders = mgr.get_pair_group(group_id)
        assert len(orders) == 2
        assert orders[0].symbol == "CL"
        assert orders[1].symbol == "BRENTOIL"
