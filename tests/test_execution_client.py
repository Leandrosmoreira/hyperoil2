"""Tests for the Hyperliquid execution client."""

from __future__ import annotations

import pytest

from hyperoil.config import ExecutionConfig
from hyperoil.execution.client import HyperliquidClient, OrderResult
from hyperoil.types import OrderSide


def _paper_client() -> HyperliquidClient:
    config = ExecutionConfig(mode="paper")
    return HyperliquidClient(config=config)


class TestHyperliquidClient:
    @pytest.mark.asyncio
    async def test_paper_connect(self) -> None:
        client = _paper_client()
        assert not client.is_connected
        await client.connect()
        assert client.is_connected
        assert client.is_paper

    @pytest.mark.asyncio
    async def test_paper_disconnect(self) -> None:
        client = _paper_client()
        await client.connect()
        await client.disconnect()
        assert not client.is_connected

    @pytest.mark.asyncio
    async def test_paper_market_order(self) -> None:
        client = _paper_client()
        await client.connect()

        result = await client.place_market_order(
            symbol="CL", side=OrderSide.BUY, qty=1.46,
        )
        assert result.success
        assert result.status == "filled"
        assert result.exchange_oid is not None

    @pytest.mark.asyncio
    async def test_paper_market_order_with_cloid(self) -> None:
        client = _paper_client()
        await client.connect()

        result = await client.place_market_order(
            symbol="CL", side=OrderSide.SELL, qty=1.0, cloid="test-ord-001",
        )
        assert result.success
        assert result.order_id == "test-ord-001"

    @pytest.mark.asyncio
    async def test_not_connected_returns_error(self) -> None:
        client = _paper_client()
        result = await client.place_market_order(
            symbol="CL", side=OrderSide.BUY, qty=1.0,
        )
        assert not result.success
        assert result.error == "client_not_connected"

    @pytest.mark.asyncio
    async def test_paper_cancel_order(self) -> None:
        client = _paper_client()
        await client.connect()
        assert await client.cancel_order("CL", 123)

    @pytest.mark.asyncio
    async def test_paper_get_open_orders_empty(self) -> None:
        client = _paper_client()
        await client.connect()
        orders = await client.get_open_orders()
        assert orders == []

    @pytest.mark.asyncio
    async def test_paper_get_user_state_none(self) -> None:
        client = _paper_client()
        await client.connect()
        state = await client.get_user_state()
        assert state is None

    @pytest.mark.asyncio
    async def test_paper_sequential_oids(self) -> None:
        client = _paper_client()
        await client.connect()

        r1 = await client.place_market_order("CL", OrderSide.BUY, 1.0)
        r2 = await client.place_market_order("CL", OrderSide.SELL, 1.0)

        assert r1.exchange_oid is not None
        assert r2.exchange_oid is not None
        assert r2.exchange_oid > r1.exchange_oid

    def test_parse_order_result_filled(self) -> None:
        raw = {
            "status": "ok",
            "response": {
                "data": {
                    "statuses": [{"filled": {"oid": 12345}}],
                },
            },
        }
        result = HyperliquidClient._parse_order_result(raw, "cloid-1")
        assert result.success
        assert result.exchange_oid == 12345
        assert result.status == "filled"

    def test_parse_order_result_resting(self) -> None:
        raw = {
            "status": "ok",
            "response": {
                "data": {
                    "statuses": [{"resting": {"oid": 67890}}],
                },
            },
        }
        result = HyperliquidClient._parse_order_result(raw, "cloid-2")
        assert result.success
        assert result.exchange_oid == 67890
        assert result.status == "resting"

    def test_parse_order_result_error(self) -> None:
        raw = {
            "status": "ok",
            "response": {
                "data": {
                    "statuses": [{"error": "Insufficient margin"}],
                },
            },
        }
        result = HyperliquidClient._parse_order_result(raw, "cloid-3")
        assert not result.success
        assert result.error == "Insufficient margin"

    def test_parse_order_result_bad_status(self) -> None:
        raw = {"status": "err", "error": "rate limited"}
        result = HyperliquidClient._parse_order_result(raw, None)
        assert not result.success
        assert "rate limited" in (result.error or "")

    @pytest.mark.asyncio
    async def test_live_mode_requires_key(self) -> None:
        config = ExecutionConfig(mode="live")
        client = HyperliquidClient(config=config)
        with pytest.raises(ValueError, match="PRIVATE_KEY"):
            await client.connect()
