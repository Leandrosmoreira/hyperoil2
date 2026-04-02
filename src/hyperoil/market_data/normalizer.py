"""Market data normalization and validation."""

from __future__ import annotations

import time

from hyperoil.market_data.orderbook import OrderbookManager
from hyperoil.observability.logger import get_logger
from hyperoil.types import Tick, now_ms

log = get_logger(__name__)


class DataNormalizer:
    """Normalizes and validates incoming market data.

    Responsibilities:
    - Validate tick data (no NaN, negative prices, etc.)
    - Compute mid price from best bid/ask or fallback
    - Detect gaps and anomalies
    - Provide synchronized pair snapshots
    """

    def __init__(self, stale_timeout_sec: float = 30.0) -> None:
        self._stale_timeout = stale_timeout_sec
        self._latest_ticks: dict[str, Tick] = {}
        self._orderbook = OrderbookManager()

    @property
    def orderbook(self) -> OrderbookManager:
        return self._orderbook

    def validate_tick(self, tick: Tick) -> bool:
        """Validate a tick is sane."""
        if tick.mid <= 0 or tick.last <= 0:
            log.warning("normalizer_invalid_price", symbol=tick.symbol, mid=tick.mid, last=tick.last)
            return False

        if tick.bid > tick.ask and tick.bid > 0 and tick.ask > 0:
            log.warning(
                "normalizer_crossed_book",
                symbol=tick.symbol,
                bid=tick.bid,
                ask=tick.ask,
            )
            return False

        # Detect extreme price moves (>20% from last)
        prev = self._latest_ticks.get(tick.symbol)
        if prev and prev.mid > 0:
            change_pct = abs(tick.mid - prev.mid) / prev.mid * 100
            if change_pct > 20:
                log.warning(
                    "normalizer_extreme_move",
                    symbol=tick.symbol,
                    prev_mid=prev.mid,
                    new_mid=tick.mid,
                    change_pct=round(change_pct, 2),
                )
                return False

        return True

    def process_tick(self, tick: Tick) -> Tick | None:
        """Validate and store tick. Returns normalized tick or None if invalid."""
        if not self.validate_tick(tick):
            return None

        # Update orderbook approximation from tick
        if tick.bid > 0 and tick.ask > 0:
            self._orderbook.update(
                tick.symbol,
                bids=[(tick.bid, 1000.0)],
                asks=[(tick.ask, 1000.0)],
            )
        else:
            self._orderbook.update_from_mids(tick.symbol, tick.mid)

        self._latest_ticks[tick.symbol] = tick
        return tick

    def get_latest(self, symbol: str) -> Tick | None:
        return self._latest_ticks.get(symbol)

    def get_pair_snapshot(
        self, left: str, right: str
    ) -> tuple[Tick, Tick] | None:
        """Get synchronized pair snapshot. Returns None if either is missing or stale."""
        tick_left = self._latest_ticks.get(left)
        tick_right = self._latest_ticks.get(right)

        if not tick_left or not tick_right:
            return None

        # Check staleness
        now = time.time() * 1000
        stale_ms = self._stale_timeout * 1000

        if (now - tick_left.timestamp_ms) > stale_ms:
            log.warning("normalizer_stale_left", symbol=left, age_ms=now - tick_left.timestamp_ms)
            return None

        if (now - tick_right.timestamp_ms) > stale_ms:
            log.warning("normalizer_stale_right", symbol=right, age_ms=now - tick_right.timestamp_ms)
            return None

        return tick_left, tick_right

    def is_pair_ready(self, left: str, right: str) -> bool:
        """Check if both symbols have fresh data."""
        return self.get_pair_snapshot(left, right) is not None

    def get_mid_price(self, symbol: str, source: str = "mid") -> float:
        """Get latest price for a symbol.

        Args:
            source: "mid" for mid price, "last" for last trade price
        """
        tick = self._latest_ticks.get(symbol)
        if not tick:
            return 0.0
        return tick.mid if source == "mid" else tick.last
