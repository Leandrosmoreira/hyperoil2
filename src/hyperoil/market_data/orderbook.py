"""L2 orderbook state manager for slippage estimation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class BookLevel:
    """Single price level in the orderbook."""
    price: float
    size: float


@dataclass
class BookSnapshot:
    """L2 orderbook snapshot for one symbol."""
    symbol: str
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    updated_at: float = 0.0

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        return 0.0

    @property
    def spread_bps(self) -> float:
        """Spread in basis points."""
        mid = self.mid_price
        if mid <= 0:
            return float("inf")
        return ((self.best_ask - self.best_bid) / mid) * 10000

    def estimated_slippage_bps(self, notional_usd: float) -> float:
        """Estimate slippage for a given notional in basis points.

        Walks the book to find how much price impact the order would have.
        """
        mid = self.mid_price
        if mid <= 0 or notional_usd <= 0:
            return 0.0

        # Walk asks for a buy
        remaining = notional_usd
        weighted_price = 0.0
        total_filled = 0.0

        for level in self.asks:
            level_notional = level.price * level.size
            fill = min(remaining, level_notional)
            fill_size = fill / level.price if level.price > 0 else 0

            weighted_price += level.price * fill_size
            total_filled += fill_size
            remaining -= fill

            if remaining <= 0:
                break

        if total_filled <= 0:
            return float("inf")

        avg_fill = weighted_price / total_filled
        return ((avg_fill - mid) / mid) * 10000

    @property
    def is_valid(self) -> bool:
        return len(self.bids) > 0 and len(self.asks) > 0 and self.best_bid < self.best_ask


class OrderbookManager:
    """Manages L2 orderbook state for multiple symbols."""

    def __init__(self) -> None:
        self._books: dict[str, BookSnapshot] = {}

    def update(
        self,
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
    ) -> None:
        """Update orderbook for a symbol.

        Args:
            bids: List of (price, size) sorted descending by price
            asks: List of (price, size) sorted ascending by price
        """
        self._books[symbol] = BookSnapshot(
            symbol=symbol,
            bids=[BookLevel(price=p, size=s) for p, s in bids],
            asks=[BookLevel(price=p, size=s) for p, s in asks],
            updated_at=time.time(),
        )

    def update_from_mids(self, symbol: str, mid: float, half_spread_pct: float = 0.01) -> None:
        """Create approximate book from mid price (when no L2 data)."""
        half = mid * half_spread_pct / 100
        self._books[symbol] = BookSnapshot(
            symbol=symbol,
            bids=[BookLevel(price=mid - half, size=1000.0)],
            asks=[BookLevel(price=mid + half, size=1000.0)],
            updated_at=time.time(),
        )

    def get(self, symbol: str) -> BookSnapshot | None:
        return self._books.get(symbol)

    def get_mid(self, symbol: str) -> float:
        book = self._books.get(symbol)
        return book.mid_price if book else 0.0

    def get_spread_bps(self, symbol: str) -> float:
        book = self._books.get(symbol)
        return book.spread_bps if book else float("inf")

    def is_stale(self, symbol: str, max_age_sec: float = 30.0) -> bool:
        book = self._books.get(symbol)
        if not book:
            return True
        return (time.time() - book.updated_at) > max_age_sec
