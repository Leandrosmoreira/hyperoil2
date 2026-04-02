"""Tests for reconciliation module."""

from __future__ import annotations

import pytest

from hyperoil.config import ExecutionConfig, SymbolsConfig
from hyperoil.execution.client import HyperliquidClient
from hyperoil.execution.order_manager import OrderManager
from hyperoil.execution.reconcile import Reconciler


def _make_reconciler() -> tuple[Reconciler, OrderManager, HyperliquidClient]:
    config = ExecutionConfig(mode="paper")
    client = HyperliquidClient(config=config)
    symbols = SymbolsConfig(left="CL", right="BRENTOIL")
    order_mgr = OrderManager(client=client, config=config, symbols=symbols)
    reconciler = Reconciler(client=client, order_manager=order_mgr)
    return reconciler, order_mgr, client


class TestReconciler:
    @pytest.mark.asyncio
    async def test_paper_reconcile_always_matches(self) -> None:
        reconciler, _, client = _make_reconciler()
        await client.connect()

        result = await reconciler.reconcile()

        assert result.positions_match
        assert result.orders_match
        assert result.stale_orders_cancelled == 0
        assert result.unknown_positions == []

    @pytest.mark.asyncio
    async def test_reconcile_updates_timestamp(self) -> None:
        reconciler, _, client = _make_reconciler()
        await client.connect()

        assert reconciler.last_reconcile_ms == 0
        await reconciler.reconcile()
        assert reconciler.last_reconcile_ms > 0

    @pytest.mark.asyncio
    async def test_reconcile_with_tracked_symbols(self) -> None:
        reconciler, _, client = _make_reconciler()
        await client.connect()

        result = await reconciler.reconcile(tracked_symbols={"CL", "BRENTOIL"})
        assert result.positions_match

    @pytest.mark.asyncio
    async def test_fetch_recent_fills_paper(self) -> None:
        reconciler, _, client = _make_reconciler()
        await client.connect()

        fills = await reconciler.fetch_recent_fills(start_time_ms=0)
        assert fills == []
