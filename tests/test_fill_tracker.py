"""Tests for the fill tracker."""

from __future__ import annotations

from hyperoil.config import BacktestConfig
from hyperoil.execution.fill_tracker import Fill, FillTracker
from hyperoil.types import OrderSide


class TestFill:
    def test_slippage_bps_buy(self) -> None:
        fill = Fill(
            order_id="o1", symbol="CL", side=OrderSide.BUY,
            qty=1.0, price=68.55, fee=0.01,
            timestamp_ms=0, mid_price_at_order=68.50,
        )
        # Bought 5 cents above mid on $68.50 ≈ 7.3 bps
        assert abs(fill.slippage_bps - 7.30) < 0.1

    def test_slippage_bps_sell(self) -> None:
        fill = Fill(
            order_id="o2", symbol="CL", side=OrderSide.SELL,
            qty=1.0, price=68.45, fee=0.01,
            timestamp_ms=0, mid_price_at_order=68.50,
        )
        # Sold 5 cents below mid on $68.50 ≈ 7.3 bps
        assert abs(fill.slippage_bps - 7.30) < 0.1

    def test_slippage_bps_no_mid(self) -> None:
        fill = Fill(
            order_id="o3", symbol="CL", side=OrderSide.BUY,
            qty=1.0, price=68.50, fee=0.01,
            timestamp_ms=0, mid_price_at_order=0.0,
        )
        assert fill.slippage_bps == 0.0

    def test_slippage_bps_zero_slippage(self) -> None:
        fill = Fill(
            order_id="o4", symbol="CL", side=OrderSide.BUY,
            qty=1.0, price=68.50, fee=0.01,
            timestamp_ms=0, mid_price_at_order=68.50,
        )
        assert abs(fill.slippage_bps) < 0.01


class TestFillTracker:
    def test_record_fill(self) -> None:
        tracker = FillTracker()
        tracker.register_mid_price("ord-1", 68.50)

        fill = tracker.record_fill(
            order_id="ord-1", cycle_id="cycle-1",
            symbol="CL", side=OrderSide.BUY,
            qty=1.46, price=68.55, fee=0.24,
        )

        assert fill.order_id == "ord-1"
        assert fill.mid_price_at_order == 68.50
        assert len(tracker.get_order_fills("ord-1")) == 1
        assert len(tracker.get_cycle_fills("cycle-1")) == 1

    def test_simulate_fill_applies_slippage(self) -> None:
        cfg = BacktestConfig(
            slippage_fixed_bps=1.0,
            slippage_proportional_bps=0.5,
            fee_taker_bps=3.5,
        )
        tracker = FillTracker(backtest_config=cfg)

        fill = tracker.simulate_fill(
            order_id="ord-1", cycle_id="cycle-1",
            symbol="CL", side=OrderSide.BUY,
            qty=1.0, mid_price=68.50,
        )

        # Buy should be above mid (slippage = 1.0 + 0.5 = 1.5 bps)
        assert fill.price > 68.50
        expected_price = 68.50 * (1 + 1.5 / 10_000)
        assert abs(fill.price - round(expected_price, 6)) < 0.001

    def test_simulate_fill_sell_below_mid(self) -> None:
        cfg = BacktestConfig(
            slippage_fixed_bps=1.0,
            slippage_proportional_bps=0.5,
        )
        tracker = FillTracker(backtest_config=cfg)

        fill = tracker.simulate_fill(
            order_id="ord-2", cycle_id="cycle-1",
            symbol="CL", side=OrderSide.SELL,
            qty=1.0, mid_price=68.50,
        )

        # Sell should be below mid
        assert fill.price < 68.50

    def test_simulate_fill_computes_fee(self) -> None:
        cfg = BacktestConfig(fee_taker_bps=3.5)
        tracker = FillTracker(backtest_config=cfg)

        fill = tracker.simulate_fill(
            order_id="ord-3", cycle_id="cycle-1",
            symbol="CL", side=OrderSide.BUY,
            qty=1.0, mid_price=100.0,
        )

        # Fee ≈ 1.0 * ~100.0 * 3.5/10000 ≈ 0.035
        assert 0.03 < fill.fee < 0.04

    def test_cycle_summary(self) -> None:
        tracker = FillTracker()
        tracker.register_mid_price("ord-1", 68.50)
        tracker.register_mid_price("ord-2", 72.30)

        tracker.record_fill(
            "ord-1", "cycle-1", "CL", OrderSide.BUY, 1.46, 68.55, 0.24,
        )
        tracker.record_fill(
            "ord-2", "cycle-1", "BRENTOIL", OrderSide.SELL, 1.38, 72.25, 0.22,
        )

        summary = tracker.get_cycle_summary("cycle-1")
        assert summary.total_fills == 2
        assert abs(summary.total_fees - 0.46) < 0.01
        assert summary.total_notional > 0
        assert summary.avg_slippage_bps != 0  # has slippage data

    def test_get_total_fees(self) -> None:
        tracker = FillTracker()
        tracker.record_fill("o1", "c1", "CL", OrderSide.BUY, 1.0, 68.50, 0.10)
        tracker.record_fill("o2", "c1", "BRENT", OrderSide.SELL, 1.0, 72.30, 0.12)

        assert abs(tracker.get_total_fees("c1") - 0.22) < 0.001

    def test_cleanup_cycle(self) -> None:
        tracker = FillTracker()
        tracker.register_mid_price("o1", 68.50)
        tracker.record_fill("o1", "c1", "CL", OrderSide.BUY, 1.0, 68.50, 0.10)

        assert len(tracker.get_cycle_fills("c1")) == 1

        tracker.cleanup_cycle("c1")
        assert len(tracker.get_cycle_fills("c1")) == 0
        assert len(tracker.get_order_fills("o1")) == 0

    def test_default_backtest_config(self) -> None:
        tracker = FillTracker()
        fill = tracker.simulate_fill(
            order_id="ord-1", cycle_id="cycle-1",
            symbol="CL", side=OrderSide.BUY,
            qty=1.0, mid_price=68.50,
        )
        # Should use BacktestConfig defaults without error
        assert fill.price > 0
        assert fill.fee > 0
