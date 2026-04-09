"""Stateful portfolio manager for the Donchian strategy.

Owns the live state (cash, positions, equity, drawdown) and exposes the
operations the orchestrator and decision engine need:

    update_prices(prices, timestamp)         mark-to-market all positions
    update_trailing_stops(signals)           ratchet stops via Sprint 2's rule
    open_position(...)                       new long entry (long-only for E1)
    increase_position(symbol, new_size, ...) scale UP an existing position
    decrease_position(symbol, new_size, ...) scale DOWN an existing position
    close_position(symbol, ...)              fully exit a position
    snapshot()                               immutable PortfolioSnapshot

Equity model: see `PortfolioState` docstring. The portfolio is long-only for
E1 — short side enters in E2/E3.
"""

from __future__ import annotations

from hyperoil.donchian.signals.trailing_stop import update_trailing_stop
from hyperoil.donchian.strategy.portfolio_state import PortfolioState
from hyperoil.donchian.types import (
    DonchianPosition,
    DonchianSignal,
    PortfolioSnapshot,
)
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


class PortfolioManager:
    """Mutable holder of portfolio state. NOT thread-safe — single-writer."""

    def __init__(
        self,
        initial_capital: float,
        fee_maker_bps: float = 1.5,
        fee_taker_bps: float = 5.0,
        slippage_bps: float = 1.0,
    ) -> None:
        if initial_capital <= 0:
            raise ValueError(f"initial_capital must be > 0, got {initial_capital}")
        self.fee_maker_bps = fee_maker_bps
        self.fee_taker_bps = fee_taker_bps
        self.slippage_bps = slippage_bps
        self.state = PortfolioState(
            cash=initial_capital,
            equity=initial_capital,
            peak_equity=initial_capital,
            drawdown_pct=0.0,
        )

    # ------------------------------------------------------------------
    # Read accessors
    # ------------------------------------------------------------------
    @property
    def positions(self) -> dict[str, DonchianPosition]:
        return self.state.positions

    @property
    def equity(self) -> float:
        return self.state.equity

    @property
    def cash(self) -> float:
        return self.state.cash

    @property
    def drawdown_pct(self) -> float:
        return self.state.drawdown_pct

    def n_open_positions(self) -> int:
        return len(self.state.positions)

    def total_exposure_usd(self) -> float:
        return sum(p.size_usd for p in self.state.positions.values())

    def get_position(self, symbol: str) -> DonchianPosition | None:
        return self.state.positions.get(symbol)

    # ------------------------------------------------------------------
    # Mark-to-market
    # ------------------------------------------------------------------
    def update_prices(self, prices: dict[str, float], timestamp_ms: int) -> None:
        """Recompute current_price + unrealized_pnl for every open position,
        then refresh equity, peak, and drawdown."""
        self.state.timestamp_ms = timestamp_ms
        for sym, pos in self.state.positions.items():
            px = prices.get(sym)
            if px is None or px <= 0:
                continue  # leave the last known price; missing tick is not a fatal
            pos.current_price = px
            pos.unrealized_pnl = self._unrealized_pnl(pos)
            pos.bars_held += 1

        self._recompute_equity()

    def _unrealized_pnl(self, pos: DonchianPosition) -> float:
        """Long-only PnL: ((current/entry) - 1) × notional."""
        if pos.entry_price <= 0:
            return 0.0
        return (pos.current_price / pos.entry_price - 1.0) * pos.size_usd

    def _recompute_equity(self) -> None:
        unrealized = sum(p.unrealized_pnl for p in self.state.positions.values())
        self.state.equity = self.state.cash + unrealized
        if self.state.equity > self.state.peak_equity:
            self.state.peak_equity = self.state.equity
        peak = self.state.peak_equity
        if peak > 0:
            self.state.drawdown_pct = max(0.0, (peak - self.state.equity) / peak)
        else:
            self.state.drawdown_pct = 0.0

    # ------------------------------------------------------------------
    # Trailing stops
    # ------------------------------------------------------------------
    def update_trailing_stops(self, signals: dict[str, DonchianSignal]) -> None:
        """Ratchet trailing stops on every open position from the latest signal.

        Stop reference is `signal.stop_line` (mid of the dominant Donchian
        channel). Never recedes — see `signals.trailing_stop`.
        """
        for sym, pos in self.state.positions.items():
            sig = signals.get(sym)
            if sig is None:
                continue
            pos.trailing_stop = update_trailing_stop(pos.trailing_stop, sig.stop_line)

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------
    def open_position(
        self,
        symbol: str,
        size_usd: float,
        price: float,
        score: float,
        leverage: float,
        stop_line: float,
        timestamp_ms: int,
        is_maker: bool = True,
    ) -> None:
        """Open a new long position. Errors if one already exists for `symbol`."""
        if symbol in self.state.positions:
            raise ValueError(f"position already open for {symbol}")
        if size_usd <= 0 or price <= 0:
            raise ValueError(f"size and price must be > 0, got {size_usd}/{price}")

        fee = self._fee(size_usd, is_maker)
        self.state.cash -= fee
        self.state.fees_paid_total += fee
        self.state.n_trades_total += 1

        self.state.positions[symbol] = DonchianPosition(
            symbol=symbol,
            side="long",
            entry_price=price,
            current_price=price,
            size_usd=size_usd,
            leverage=leverage,
            trailing_stop=stop_line,
            score_at_entry=score,
            entry_timestamp_ms=timestamp_ms,
            unrealized_pnl=0.0,
            bars_held=0,
        )
        self._recompute_equity()
        log.info(
            "position_opened",
            symbol=symbol,
            size_usd=size_usd,
            price=price,
            leverage=leverage,
            stop=stop_line,
            fee=fee,
        )

    def increase_position(
        self,
        symbol: str,
        new_size_usd: float,
        price: float,
        timestamp_ms: int,
        is_maker: bool = True,
    ) -> None:
        """Scale an existing long up to `new_size_usd`. Recomputes the
        VWAP entry price so unrealized PnL stays consistent."""
        pos = self.state.positions.get(symbol)
        if pos is None:
            raise ValueError(f"no position to increase for {symbol}")
        if new_size_usd <= pos.size_usd:
            raise ValueError(
                f"increase requires new_size > current ({new_size_usd} <= {pos.size_usd})"
            )
        delta = new_size_usd - pos.size_usd
        fee = self._fee(delta, is_maker)
        self.state.cash -= fee
        self.state.fees_paid_total += fee
        self.state.n_trades_total += 1

        # VWAP entry: weight by notional contribution
        pos.entry_price = (
            pos.entry_price * pos.size_usd + price * delta
        ) / new_size_usd
        pos.size_usd = new_size_usd
        pos.current_price = price
        pos.unrealized_pnl = self._unrealized_pnl(pos)
        self._recompute_equity()
        log.info(
            "position_increased",
            symbol=symbol,
            new_size_usd=new_size_usd,
            delta=delta,
            price=price,
            fee=fee,
        )

    def decrease_position(
        self,
        symbol: str,
        new_size_usd: float,
        price: float,
        timestamp_ms: int,
        is_maker: bool = True,
    ) -> None:
        """Scale an existing long down to `new_size_usd`. Realizes a
        proportional slice of unrealized PnL on the closed-out portion."""
        pos = self.state.positions.get(symbol)
        if pos is None:
            raise ValueError(f"no position to decrease for {symbol}")
        if new_size_usd <= 0 or new_size_usd >= pos.size_usd:
            raise ValueError(
                f"decrease requires 0 < new_size < current "
                f"({new_size_usd} not in (0, {pos.size_usd}))"
            )
        closed_notional = pos.size_usd - new_size_usd
        # Realized PnL on the closed slice (priced at execution `price`)
        realized = (price / pos.entry_price - 1.0) * closed_notional
        fee = self._fee(closed_notional, is_maker)

        self.state.cash += realized - fee
        self.state.realized_pnl_total += realized
        self.state.fees_paid_total += fee
        self.state.n_trades_total += 1

        pos.size_usd = new_size_usd
        pos.current_price = price
        pos.unrealized_pnl = self._unrealized_pnl(pos)
        self._recompute_equity()
        log.info(
            "position_decreased",
            symbol=symbol,
            new_size_usd=new_size_usd,
            closed_notional=closed_notional,
            realized=realized,
            fee=fee,
        )

    def close_position(
        self,
        symbol: str,
        price: float,
        timestamp_ms: int,
        is_maker: bool = True,
        reason: str = "",
    ) -> float:
        """Fully exit a position. Returns realized PnL on the close."""
        pos = self.state.positions.get(symbol)
        if pos is None:
            raise ValueError(f"no position to close for {symbol}")

        realized = (price / pos.entry_price - 1.0) * pos.size_usd
        fee = self._fee(pos.size_usd, is_maker)

        self.state.cash += realized - fee
        self.state.realized_pnl_total += realized
        self.state.fees_paid_total += fee
        self.state.n_trades_total += 1

        del self.state.positions[symbol]
        self._recompute_equity()
        log.info(
            "position_closed",
            symbol=symbol,
            realized=realized,
            fee=fee,
            reason=reason,
        )
        return realized

    # ------------------------------------------------------------------
    # Fees + telemetry
    # ------------------------------------------------------------------
    def _fee(self, notional: float, is_maker: bool) -> float:
        bps = self.fee_maker_bps if is_maker else self.fee_taker_bps
        return notional * bps / 10_000.0

    def snapshot(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            timestamp_ms=self.state.timestamp_ms,
            equity=self.state.equity,
            cash=self.state.cash,
            peak_equity=self.state.peak_equity,
            drawdown_pct=self.state.drawdown_pct,
            n_positions=len(self.state.positions),
            total_exposure_usd=self.total_exposure_usd(),
            positions=dict(self.state.positions),
        )
