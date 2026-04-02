"""Tests for market data normalizer."""

from __future__ import annotations

from hyperoil.market_data.normalizer import DataNormalizer
from hyperoil.types import Tick, now_ms


def _make_tick(
    symbol: str = "CL",
    bid: float = 68.50,
    ask: float = 68.52,
    mid: float = 68.51,
    last: float = 68.51,
    ts: int | None = None,
) -> Tick:
    return Tick(
        timestamp_ms=ts or now_ms(),
        symbol=symbol,
        bid=bid,
        ask=ask,
        mid=mid,
        last=last,
    )


class TestDataNormalizer:
    def test_valid_tick_passes(self) -> None:
        norm = DataNormalizer()
        tick = _make_tick()
        result = norm.process_tick(tick)
        assert result is not None
        assert result.symbol == "CL"

    def test_zero_price_rejected(self) -> None:
        norm = DataNormalizer()
        tick = _make_tick(mid=0.0, last=0.0)
        result = norm.process_tick(tick)
        assert result is None

    def test_negative_price_rejected(self) -> None:
        norm = DataNormalizer()
        tick = _make_tick(mid=-5.0, last=-5.0)
        result = norm.process_tick(tick)
        assert result is None

    def test_crossed_book_rejected(self) -> None:
        norm = DataNormalizer()
        tick = _make_tick(bid=68.55, ask=68.50)  # bid > ask
        result = norm.process_tick(tick)
        assert result is None

    def test_extreme_move_rejected(self) -> None:
        norm = DataNormalizer()
        # First tick establishes baseline
        tick1 = _make_tick(mid=68.51, last=68.51)
        assert norm.process_tick(tick1) is not None

        # 25% jump should be rejected
        tick2 = _make_tick(mid=85.0, last=85.0)
        assert norm.process_tick(tick2) is None

    def test_normal_move_accepted(self) -> None:
        norm = DataNormalizer()
        tick1 = _make_tick(mid=68.51, last=68.51)
        assert norm.process_tick(tick1) is not None

        # 1% move is fine
        tick2 = _make_tick(mid=69.20, last=69.20)
        assert norm.process_tick(tick2) is not None

    def test_get_latest(self) -> None:
        norm = DataNormalizer()
        tick = _make_tick()
        norm.process_tick(tick)
        assert norm.get_latest("CL") is not None
        assert norm.get_latest("BRENTOIL") is None

    def test_pair_snapshot_both_present(self) -> None:
        norm = DataNormalizer()
        ts = now_ms()
        norm.process_tick(_make_tick("CL", ts=ts))
        norm.process_tick(_make_tick("BRENTOIL", bid=72.30, ask=72.32, mid=72.31, last=72.31, ts=ts))

        pair = norm.get_pair_snapshot("CL", "BRENTOIL")
        assert pair is not None
        left, right = pair
        assert left.symbol == "CL"
        assert right.symbol == "BRENTOIL"

    def test_pair_snapshot_missing_one(self) -> None:
        norm = DataNormalizer()
        norm.process_tick(_make_tick("CL"))
        assert norm.get_pair_snapshot("CL", "BRENTOIL") is None

    def test_is_pair_ready(self) -> None:
        norm = DataNormalizer()
        ts = now_ms()
        assert not norm.is_pair_ready("CL", "BRENTOIL")

        norm.process_tick(_make_tick("CL", ts=ts))
        assert not norm.is_pair_ready("CL", "BRENTOIL")

        norm.process_tick(_make_tick("BRENTOIL", bid=72.30, ask=72.32, mid=72.31, last=72.31, ts=ts))
        assert norm.is_pair_ready("CL", "BRENTOIL")

    def test_get_mid_price(self) -> None:
        norm = DataNormalizer()
        norm.process_tick(_make_tick(mid=68.51, last=68.49))
        assert norm.get_mid_price("CL", "mid") == 68.51
        assert norm.get_mid_price("CL", "last") == 68.49
        assert norm.get_mid_price("UNKNOWN") == 0.0

    def test_orderbook_updated_on_tick(self) -> None:
        norm = DataNormalizer()
        norm.process_tick(_make_tick())
        assert norm.orderbook.get_mid("CL") > 0
