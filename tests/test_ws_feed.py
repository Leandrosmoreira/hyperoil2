"""Tests for WebSocket feed state machine and message handling."""

from __future__ import annotations

import asyncio
import json

import pytest

from hyperoil.config import MarketDataConfig, SymbolsConfig
from hyperoil.market_data.ws_feed import WsFeed
from hyperoil.types import ConnectionState, Tick


@pytest.fixture
def symbols() -> SymbolsConfig:
    return SymbolsConfig(left="CL", right="BRENTOIL", dex_prefix="xyz")


@pytest.fixture
def md_config() -> MarketDataConfig:
    return MarketDataConfig()


@pytest.fixture
def feed(symbols: SymbolsConfig, md_config: MarketDataConfig) -> WsFeed:
    return WsFeed(symbols=symbols, market_data=md_config)


class TestWsFeedInit:
    def test_initial_state(self, feed: WsFeed) -> None:
        assert feed.state == ConnectionState.DISCONNECTED
        assert feed.last_msg_time == 0.0
        assert feed.mid_prices == {}

    def test_coin_name_with_prefix(self, feed: WsFeed) -> None:
        assert feed._coin_name("CL") == "xyz:CL"
        assert feed._coin_name("xyz:CL") == "xyz:CL"

    def test_state_change(self, feed: WsFeed) -> None:
        feed._set_state(ConnectionState.CONNECTING)
        assert feed.state == ConnectionState.CONNECTING

        feed._set_state(ConnectionState.CONNECTED)
        assert feed.state == ConnectionState.CONNECTED


class TestWsFeedMessageHandling:
    @pytest.mark.asyncio
    async def test_handle_candle(self, feed: WsFeed) -> None:
        received: list[Tick] = []

        async def on_tick(tick: Tick) -> None:
            received.append(tick)

        feed._on_tick = on_tick

        await feed._handle_message({
            "channel": "candle",
            "data": {
                "s": "xyz:CL",
                "T": 1700000000000,
                "o": "68.50",
                "h": "68.60",
                "l": "68.40",
                "c": "68.55",
                "v": "1000",
                "n": 50,
            },
        })

        assert len(received) == 1
        assert received[0].symbol == "CL"
        assert received[0].last == 68.55

    @pytest.mark.asyncio
    async def test_handle_candle_invalid_data(self, feed: WsFeed) -> None:
        received: list[Tick] = []

        async def on_tick(tick: Tick) -> None:
            received.append(tick)

        feed._on_tick = on_tick

        # Missing required fields
        await feed._handle_message({
            "channel": "candle",
            "data": {"s": "xyz:CL"},
        })
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_handle_all_mids(self, feed: WsFeed) -> None:
        await feed._handle_message({
            "channel": "allMids",
            "data": {
                "mids": {
                    "xyz:CL": "68.51",
                    "xyz:BRENTOIL": "72.31",
                },
            },
        })

        assert feed.mid_prices["CL"] == 68.51
        assert feed.mid_prices["BRENTOIL"] == 72.31

    @pytest.mark.asyncio
    async def test_new_candle_detection(self, feed: WsFeed) -> None:
        candles: list[dict] = []

        async def on_candle(**kwargs: object) -> None:
            candles.append(dict(kwargs))

        feed._on_candle = on_candle
        feed._on_tick = None  # no tick callback needed

        # First candle — sets baseline, no callback
        await feed._handle_candle({
            "s": "xyz:CL", "T": 1000, "o": "68", "h": "69", "l": "67", "c": "68.5", "v": "100",
        })
        assert len(candles) == 0

        # Same close_time — update, no new candle
        await feed._handle_candle({
            "s": "xyz:CL", "T": 1000, "o": "68", "h": "69", "l": "67", "c": "68.6", "v": "110",
        })
        assert len(candles) == 0

        # New close_time — new candle detected
        await feed._handle_candle({
            "s": "xyz:CL", "T": 2000, "o": "68.5", "h": "69.5", "l": "68", "c": "69.0", "v": "200",
        })
        assert len(candles) == 1
        assert candles[0]["symbol"] == "CL"
        assert candles[0]["close"] == 69.0


class TestWsFeedCircuitBreaker:
    @pytest.mark.asyncio
    async def test_unknown_channel_ignored(self, feed: WsFeed) -> None:
        # Should not raise
        await feed._handle_message({"channel": "unknown", "data": {}})

    @pytest.mark.asyncio
    async def test_subscription_response_handled(self, feed: WsFeed) -> None:
        await feed._handle_message({"channel": "subscriptionResponse", "data": {"subscribed": True}})
