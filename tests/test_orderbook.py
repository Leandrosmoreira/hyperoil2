"""Tests for L2 orderbook state manager."""

from __future__ import annotations

import time

from hyperoil.market_data.orderbook import BookLevel, BookSnapshot, OrderbookManager


class TestBookSnapshot:
    def test_mid_price(self) -> None:
        book = BookSnapshot(
            symbol="CL",
            bids=[BookLevel(price=68.50, size=100)],
            asks=[BookLevel(price=68.52, size=100)],
        )
        assert abs(book.mid_price - 68.51) < 1e-9

    def test_best_bid_ask(self) -> None:
        book = BookSnapshot(
            symbol="CL",
            bids=[BookLevel(price=68.50, size=100), BookLevel(price=68.48, size=200)],
            asks=[BookLevel(price=68.52, size=100), BookLevel(price=68.54, size=200)],
        )
        assert book.best_bid == 68.50
        assert book.best_ask == 68.52

    def test_spread_bps(self) -> None:
        book = BookSnapshot(
            symbol="CL",
            bids=[BookLevel(price=68.50, size=100)],
            asks=[BookLevel(price=68.52, size=100)],
        )
        # spread = 0.02, mid = 68.51, bps = (0.02/68.51)*10000 ≈ 2.92
        assert 2.9 < book.spread_bps < 3.0

    def test_spread_bps_empty_book(self) -> None:
        book = BookSnapshot(symbol="CL")
        assert book.spread_bps == float("inf")

    def test_is_valid(self) -> None:
        valid = BookSnapshot(
            symbol="CL",
            bids=[BookLevel(price=68.50, size=100)],
            asks=[BookLevel(price=68.52, size=100)],
        )
        assert valid.is_valid

        empty = BookSnapshot(symbol="CL")
        assert not empty.is_valid

        crossed = BookSnapshot(
            symbol="CL",
            bids=[BookLevel(price=68.54, size=100)],
            asks=[BookLevel(price=68.50, size=100)],
        )
        assert not crossed.is_valid

    def test_estimated_slippage_single_level(self) -> None:
        book = BookSnapshot(
            symbol="CL",
            bids=[BookLevel(price=68.50, size=1000)],
            asks=[BookLevel(price=68.52, size=1000)],
        )
        # For small notional, slippage should be the half-spread
        slippage = book.estimated_slippage_bps(100.0)
        assert slippage > 0
        # ask is 68.52, mid is 68.51 → about 0.73 bps
        assert slippage < 2.0

    def test_estimated_slippage_walks_book(self) -> None:
        book = BookSnapshot(
            symbol="CL",
            bids=[BookLevel(price=68.50, size=10)],
            asks=[
                BookLevel(price=68.52, size=1),   # ~68.52 notional
                BookLevel(price=68.60, size=1),   # ~68.60 notional
                BookLevel(price=69.00, size=100),  # plenty of liquidity
            ],
        )
        # Large order should walk deeper into the book
        small_slip = book.estimated_slippage_bps(50.0)
        large_slip = book.estimated_slippage_bps(5000.0)
        assert large_slip > small_slip


class TestOrderbookManager:
    def test_update_and_get(self) -> None:
        mgr = OrderbookManager()
        mgr.update("CL", bids=[(68.50, 100)], asks=[(68.52, 100)])

        book = mgr.get("CL")
        assert book is not None
        assert abs(book.mid_price - 68.51) < 1e-9

    def test_get_mid(self) -> None:
        mgr = OrderbookManager()
        mgr.update("CL", bids=[(68.50, 100)], asks=[(68.52, 100)])
        assert abs(mgr.get_mid("CL") - 68.51) < 1e-9
        assert mgr.get_mid("UNKNOWN") == 0.0

    def test_get_spread_bps(self) -> None:
        mgr = OrderbookManager()
        mgr.update("CL", bids=[(68.50, 100)], asks=[(68.52, 100)])
        assert mgr.get_spread_bps("CL") < 5.0
        assert mgr.get_spread_bps("UNKNOWN") == float("inf")

    def test_update_from_mids(self) -> None:
        mgr = OrderbookManager()
        mgr.update_from_mids("CL", mid=68.51)
        book = mgr.get("CL")
        assert book is not None
        assert abs(book.mid_price - 68.51) < 0.01

    def test_is_stale(self) -> None:
        mgr = OrderbookManager()
        assert mgr.is_stale("CL")  # no data = stale

        mgr.update("CL", bids=[(68.50, 100)], asks=[(68.52, 100)])
        assert not mgr.is_stale("CL", max_age_sec=30.0)
