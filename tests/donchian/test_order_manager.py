"""Tests for SingleOrderManager — the single-asset Donchian execution facade."""

from __future__ import annotations

import pytest

from hyperoil.config import ExecutionConfig
from hyperoil.donchian.config import OrderPolicyConfig
from hyperoil.donchian.execution.order_manager import (
    SingleOrderManager,
    round_down_qty,
)
from hyperoil.execution.client import HyperliquidClient


def _make_mgr(policy: OrderPolicyConfig | None = None) -> SingleOrderManager:
    client = HyperliquidClient(config=ExecutionConfig(mode="paper"))
    # Paper mode `connect` is sync-friendly: no SDK init.
    client._connected = True  # type: ignore[attr-defined]
    return SingleOrderManager(
        client=client,
        policy=policy or OrderPolicyConfig(),
        sz_decimals={"BTC": 5, "GOLD": 3, "TINY": 0},
    )


# ------------------------------------------------------------------
# round_down_qty
# ------------------------------------------------------------------
def test_round_down_qty_truncates_not_rounds():
    # 100 / 27 = 3.7037037..., 3 decimals -> 3.703 (NOT 3.704)
    assert round_down_qty(100.0, 27.0, 3) == 3.703


def test_round_down_qty_zero_when_below_lot_size():
    # 1 / 50000 = 0.00002 → 0 decimals → 0
    assert round_down_qty(1.0, 50000.0, 0) == 0.0


def test_round_down_qty_handles_zero_price_or_notional():
    assert round_down_qty(100.0, 0.0, 3) == 0.0
    assert round_down_qty(0.0, 100.0, 3) == 0.0


# ------------------------------------------------------------------
# Order routing
# ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_enter_uses_post_only_in_paper_mode():
    mgr = _make_mgr()
    out = await mgr.execute_enter(symbol="hyna:BTC", notional_usd=1000.0, price=50000.0)
    assert out.success
    assert out.order_type == "limit_maker"
    assert out.qty > 0
    # 1000 / 50000 = 0.02, sz_decimals=5 → exact
    assert out.qty == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_increase_decrease_route_post_only():
    mgr = _make_mgr()
    inc = await mgr.execute_increase(symbol="xyz:GOLD", delta_notional_usd=500.0, price=2000.0)
    dec = await mgr.execute_decrease(symbol="xyz:GOLD", delta_notional_usd=300.0, price=2000.0)
    assert inc.order_type == "limit_maker"
    assert dec.order_type == "limit_maker"
    assert inc.success and dec.success


@pytest.mark.asyncio
async def test_normal_exit_is_post_only():
    mgr = _make_mgr()
    out = await mgr.execute_exit(
        symbol="hyna:BTC", notional_usd=500.0, price=50000.0, reason="regime_change",
    )
    assert out.success
    assert out.order_type == "limit_maker"


@pytest.mark.asyncio
async def test_stop_hit_exit_routes_to_market_when_policy_allows():
    policy = OrderPolicyConfig(emergency_exit_order_type="market")
    mgr = _make_mgr(policy)
    out = await mgr.execute_exit(
        symbol="hyna:BTC", notional_usd=500.0, price=50000.0, reason="stop_hit",
    )
    assert out.success
    assert out.order_type == "market"


@pytest.mark.asyncio
async def test_qty_rounded_to_zero_returns_failure():
    mgr = _make_mgr()
    # TINY has sz_decimals=0; 1 USD / 50000 = 0.00002 → floor = 0
    out = await mgr.execute_enter(symbol="xyz:TINY", notional_usd=1.0, price=50000.0)
    assert not out.success
    assert out.error == "qty_rounded_to_zero"
    assert out.qty == 0.0


@pytest.mark.asyncio
async def test_ticker_strips_dex_prefix():
    """The HL SDK takes the bare ticker (no `xyz:` / `hyna:` prefix)."""
    assert SingleOrderManager._ticker("hyna:BTC") == "BTC"
    assert SingleOrderManager._ticker("xyz:GOLD") == "GOLD"
    # Unprefixed input passes through.
    assert SingleOrderManager._ticker("BTC") == "BTC"
