"""Unit tests for MultiAssetWsFeed bar-close logic.

The WebSocket itself is not exercised — we drive `_handle_candle` directly
with synthetic Hyperliquid candle messages and assert the bar-close
callback fires exactly once per advanced T per symbol.
"""

from __future__ import annotations

import pytest

from hyperoil.config import MarketDataConfig
from hyperoil.donchian.config import AssetConfig
from hyperoil.donchian.core.ws_multi_feed import MultiAssetWsFeed
from hyperoil.donchian.types import AssetClass


def _asset(prefix: str, ticker: str) -> AssetConfig:
    return AssetConfig(
        symbol=ticker, hl_ticker=ticker, dex_prefix=prefix,
        asset_class=AssetClass.CRYPTO_MAJOR,
    )


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, sym: str, bar: dict) -> None:
        self.events.append((sym, dict(bar)))


def _make_feed(rec: _Recorder) -> MultiAssetWsFeed:
    return MultiAssetWsFeed(
        assets=[_asset("hyna", "BTC"), _asset("hyna", "ETH")],
        market_data=MarketDataConfig(),
        on_bar_close=rec,
    )


def _candle(sym: str, T: int, close: float = 100.0) -> dict:
    return {"s": sym, "T": T, "o": close, "h": close, "l": close, "c": close, "v": 1.0}


@pytest.mark.asyncio
async def test_first_message_seeds_state_no_callback():
    rec = _Recorder()
    feed = _make_feed(rec)
    await feed._handle_candle(_candle("hyna:BTC", 1000, close=50000.0))
    assert rec.events == []
    assert feed._last_close_time["hyna:BTC"] == 1000


@pytest.mark.asyncio
async def test_same_T_updates_partial_in_place():
    rec = _Recorder()
    feed = _make_feed(rec)
    await feed._handle_candle(_candle("hyna:BTC", 1000, close=50000.0))
    await feed._handle_candle(_candle("hyna:BTC", 1000, close=50100.0))
    await feed._handle_candle(_candle("hyna:BTC", 1000, close=50200.0))
    assert rec.events == []
    assert feed._partial["hyna:BTC"]["close"] == 50200.0


@pytest.mark.asyncio
async def test_T_advance_emits_previous_bar_with_prev_T_timestamp():
    rec = _Recorder()
    feed = _make_feed(rec)
    await feed._handle_candle(_candle("hyna:BTC", 1000, close=50000.0))
    await feed._handle_candle(_candle("hyna:BTC", 1000, close=50500.0))
    await feed._handle_candle(_candle("hyna:BTC", 2000, close=51000.0))
    assert len(rec.events) == 1
    sym, bar = rec.events[0]
    assert sym == "hyna:BTC"
    # The closed-out bar carries the PREVIOUS T as its timestamp, not the new one.
    assert bar["timestamp_ms"] == 1000
    # The close should be the LAST partial value before advance, not the new bar's open.
    assert bar["close"] == 50500.0


@pytest.mark.asyncio
async def test_per_symbol_isolation():
    """A T-advance on BTC must NOT close out an ETH bar."""
    rec = _Recorder()
    feed = _make_feed(rec)
    await feed._handle_candle(_candle("hyna:BTC", 1000))
    await feed._handle_candle(_candle("hyna:ETH", 1000))
    await feed._handle_candle(_candle("hyna:BTC", 2000))   # only BTC closes
    assert len(rec.events) == 1
    assert rec.events[0][0] == "hyna:BTC"


@pytest.mark.asyncio
async def test_invalid_candle_payload_does_not_raise():
    rec = _Recorder()
    feed = _make_feed(rec)
    # Missing required fields → silently ignored, no exception, no callback.
    await feed._handle_candle({"s": "hyna:BTC", "T": 1000})  # no OHLC
    assert rec.events == []


def test_constructor_rejects_empty_universe():
    with pytest.raises(ValueError):
        MultiAssetWsFeed(
            assets=[],
            market_data=MarketDataConfig(),
            on_bar_close=_Recorder(),
        )
