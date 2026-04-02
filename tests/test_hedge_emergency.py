"""Tests for hedge emergency module."""

from __future__ import annotations

import pytest

from hyperoil.config import ExecutionConfig, SymbolsConfig
from hyperoil.execution.client import HyperliquidClient
from hyperoil.execution.hedge_emergency import HedgeEmergency
from hyperoil.execution.order_manager import OrderManager
from hyperoil.types import Direction, OrderStatus


async def _make_components() -> tuple[HedgeEmergency, OrderManager, HyperliquidClient]:
    config = ExecutionConfig(mode="paper", fill_timeout_sec=0.001, emergency_hedge=True)
    client = HyperliquidClient(config=config)
    await client.connect()
    symbols = SymbolsConfig(left="CL", right="BRENTOIL")
    order_mgr = OrderManager(client=client, config=config, symbols=symbols)
    hedge = HedgeEmergency(client=client, order_manager=order_mgr, config=config)
    return hedge, order_mgr, client


class TestHedgeEmergency:
    @pytest.mark.asyncio
    async def test_no_action_when_both_filled(self) -> None:
        hedge, order_mgr, _ = await _make_components()

        group_id, _, _ = await order_mgr.send_pair_entry(
            cycle_id="cycle-1",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        # Paper mode fills both immediately
        action = await hedge.check_group(group_id)
        assert action is None

    @pytest.mark.asyncio
    async def test_no_action_nonexistent_group(self) -> None:
        hedge, _, _ = await _make_components()
        action = await hedge.check_group("nonexistent")
        assert action is None

    @pytest.mark.asyncio
    async def test_hedge_triggered_on_one_filled_one_failed(self) -> None:
        hedge, order_mgr, _ = await _make_components()

        group_id, left, right = await order_mgr.send_pair_entry(
            cycle_id="cycle-1",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        # Simulate: left filled, right failed
        left.status = OrderStatus.FILLED
        right.status = OrderStatus.FAILED
        right.error = "simulated_failure"

        action = await hedge.check_group(group_id)
        assert action is not None
        assert action.filled_leg == "left"
        assert action.action == "hedge_market"
        assert action.success  # paper mode always succeeds

    @pytest.mark.asyncio
    async def test_cancel_both_when_both_failed(self) -> None:
        hedge, order_mgr, _ = await _make_components()

        group_id, left, right = await order_mgr.send_pair_entry(
            cycle_id="cycle-1",
            direction=Direction.LONG_SPREAD,
            size_left=1.0,
            size_right=1.0,
            level=1,
        )

        # Simulate: both failed
        left.status = OrderStatus.FAILED
        right.status = OrderStatus.FAILED

        action = await hedge.check_group(group_id)
        assert action is not None
        assert action.action == "cancel_both"

    @pytest.mark.asyncio
    async def test_hedge_disabled(self) -> None:
        config = ExecutionConfig(mode="paper", emergency_hedge=False)
        client = HyperliquidClient(config=config)
        await client.connect()
        symbols = SymbolsConfig(left="CL", right="BRENTOIL")
        order_mgr = OrderManager(client=client, config=config, symbols=symbols)
        hedge = HedgeEmergency(client=client, order_manager=order_mgr, config=config)

        group_id, left, right = await order_mgr.send_pair_entry(
            cycle_id="cycle-1",
            direction=Direction.LONG_SPREAD,
            size_left=1.0, size_right=1.0, level=1,
        )

        left.status = OrderStatus.FILLED
        right.status = OrderStatus.FAILED

        action = await hedge.check_group(group_id)
        assert action is None  # disabled

    @pytest.mark.asyncio
    async def test_actions_recorded(self) -> None:
        hedge, order_mgr, _ = await _make_components()

        group_id, left, right = await order_mgr.send_pair_entry(
            cycle_id="cycle-1",
            direction=Direction.LONG_SPREAD,
            size_left=1.0, size_right=1.0, level=1,
        )

        left.status = OrderStatus.FILLED
        right.status = OrderStatus.FAILED

        await hedge.check_group(group_id)

        assert len(hedge.actions) == 1
        assert hedge.actions[0].group_id == group_id

    @pytest.mark.asyncio
    async def test_hedge_right_filled_left_failed(self) -> None:
        hedge, order_mgr, _ = await _make_components()

        group_id, left, right = await order_mgr.send_pair_entry(
            cycle_id="cycle-1",
            direction=Direction.SHORT_SPREAD,
            size_left=1.0, size_right=1.0, level=1,
        )

        # Right filled, left failed
        right.status = OrderStatus.FILLED
        left.status = OrderStatus.FAILED

        action = await hedge.check_group(group_id)
        assert action is not None
        assert action.filled_leg == "right"
        assert action.unfilled_leg == "left"
        assert action.action == "hedge_market"
