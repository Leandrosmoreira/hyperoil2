"""Tests for the stateful Donchian portfolio manager."""

from __future__ import annotations

import math

import pytest

from hyperoil.donchian.strategy.portfolio_manager import PortfolioManager
from hyperoil.donchian.types import DonchianSignal


def _pm(capital: float = 10_000.0) -> PortfolioManager:
    return PortfolioManager(initial_capital=capital, fee_maker_bps=1.5)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------
def test_init_starts_at_full_cash():
    pm = _pm(10_000)
    assert pm.cash == 10_000
    assert pm.equity == 10_000
    assert pm.drawdown_pct == 0.0
    assert pm.n_open_positions() == 0


def test_init_rejects_zero_capital():
    with pytest.raises(ValueError, match="initial_capital"):
        PortfolioManager(initial_capital=0)


# ----------------------------------------------------------------------
# Open / close round-trip
# ----------------------------------------------------------------------
def test_open_and_close_round_trip_pnl():
    pm = _pm(10_000)
    pm.open_position("BTC", size_usd=1000, price=100.0, score=0.6,
                     leverage=1.5, stop_line=95.0, timestamp_ms=1)
    assert pm.n_open_positions() == 1
    # After opening: cash = 10000 - fee(1000 * 1.5bps) = 10000 - 0.15
    assert pm.cash == pytest.approx(10_000 - 0.15)

    # Mark to market: price up 10% → unrealized = +100
    pm.update_prices({"BTC": 110.0}, timestamp_ms=2)
    assert pm.equity == pytest.approx(10_000 - 0.15 + 100.0)
    assert pm.state.peak_equity == pm.equity

    # Close at 110 → realized = 100, fee = 1000 * 1.5 / 1e4 = 0.15
    realized = pm.close_position("BTC", price=110.0, timestamp_ms=3)
    assert realized == pytest.approx(100.0)
    # cash after = 10000 - 0.15 (open fee) + 100 (realized) - 0.15 (close fee)
    assert pm.cash == pytest.approx(10_000 - 0.15 + 100.0 - 0.15)
    assert pm.n_open_positions() == 0


def test_open_duplicate_raises():
    pm = _pm()
    pm.open_position("BTC", 1000, 100, 0.6, 1.5, 95, 1)
    with pytest.raises(ValueError, match="already open"):
        pm.open_position("BTC", 500, 100, 0.6, 1.5, 95, 2)


def test_close_missing_raises():
    pm = _pm()
    with pytest.raises(ValueError, match="no position"):
        pm.close_position("BTC", price=100, timestamp_ms=1)


# ----------------------------------------------------------------------
# Drawdown tracking
# ----------------------------------------------------------------------
def test_drawdown_tracks_peak():
    pm = _pm(10_000)
    pm.open_position("BTC", 1000, 100, 0.9, 2.0, 90, 1)
    pm.update_prices({"BTC": 130.0}, 2)   # +30% on 1000 = +300
    peak = pm.equity
    pm.update_prices({"BTC": 110.0}, 3)   # falls back to +10% on 1000 = +100
    assert pm.state.peak_equity == pytest.approx(peak)
    assert pm.drawdown_pct == pytest.approx((peak - pm.equity) / peak)


def test_drawdown_never_negative():
    pm = _pm(10_000)
    pm.open_position("BTC", 1000, 100, 0.9, 2.0, 90, 1)
    pm.update_prices({"BTC": 200.0}, 2)
    assert pm.drawdown_pct == 0.0


# ----------------------------------------------------------------------
# Increase / decrease
# ----------------------------------------------------------------------
def test_increase_recomputes_vwap_entry():
    pm = _pm(10_000)
    pm.open_position("BTC", 1000, 100.0, 0.6, 1.5, 95, 1)
    # Increase to 3000 at price 200 → VWAP = (100*1000 + 200*2000)/3000 = 166.6667
    pm.increase_position("BTC", new_size_usd=3000, price=200.0, timestamp_ms=2)
    pos = pm.get_position("BTC")
    assert pos.size_usd == 3000
    assert pos.entry_price == pytest.approx((100*1000 + 200*2000) / 3000)


def test_increase_below_current_raises():
    pm = _pm()
    pm.open_position("BTC", 1000, 100, 0.6, 1.5, 95, 1)
    with pytest.raises(ValueError, match="increase requires"):
        pm.increase_position("BTC", new_size_usd=500, price=100, timestamp_ms=2)


def test_decrease_realizes_proportional_pnl():
    pm = _pm(10_000)
    pm.open_position("BTC", 1000, 100.0, 0.6, 1.5, 95, 1)
    # Price doubles. Decrease from 1000 to 400 → closed slice = 600 notional.
    # Realized on slice = (200/100 - 1) * 600 = +600
    pm.decrease_position("BTC", new_size_usd=400, price=200.0, timestamp_ms=2)
    pos = pm.get_position("BTC")
    assert pos.size_usd == 400
    # Cash should be: 10000 - 0.15 (open fee) + 600 (realized) - fee on 600
    expected_cash = 10_000 - 0.15 + 600.0 - (600 * 1.5 / 1e4)
    assert pm.cash == pytest.approx(expected_cash)


def test_decrease_outside_range_raises():
    pm = _pm()
    pm.open_position("BTC", 1000, 100, 0.6, 1.5, 95, 1)
    with pytest.raises(ValueError, match="decrease requires"):
        pm.decrease_position("BTC", new_size_usd=2000, price=100, timestamp_ms=2)
    with pytest.raises(ValueError, match="decrease requires"):
        pm.decrease_position("BTC", new_size_usd=0, price=100, timestamp_ms=2)


# ----------------------------------------------------------------------
# Trailing stops
# ----------------------------------------------------------------------
def test_trailing_stops_ratchet_via_signal():
    pm = _pm()
    pm.open_position("BTC", 1000, 100, 0.6, 1.5, 95, 1)
    sig = DonchianSignal(symbol="BTC", timestamp_ms=2, score=0.6,
                         dominant_lookback=120, stop_line=98.0,
                         ema_200=90.0, entry_valid=True)
    pm.update_trailing_stops({"BTC": sig})
    assert pm.get_position("BTC").trailing_stop == 98.0

    # Lower stop must NOT recede
    sig_lower = DonchianSignal(symbol="BTC", timestamp_ms=3, score=0.6,
                               dominant_lookback=120, stop_line=92.0,
                               ema_200=90.0, entry_valid=True)
    pm.update_trailing_stops({"BTC": sig_lower})
    assert pm.get_position("BTC").trailing_stop == 98.0


# ----------------------------------------------------------------------
# Snapshot
# ----------------------------------------------------------------------
def test_snapshot_is_isolated_copy():
    pm = _pm()
    pm.open_position("BTC", 1000, 100, 0.6, 1.5, 95, 1)
    snap = pm.snapshot()
    assert snap.n_positions == 1
    assert snap.total_exposure_usd == 1000
    # Mutating positions on the manager must not affect the snapshot dict.
    pm.close_position("BTC", price=100, timestamp_ms=2)
    assert "BTC" in snap.positions
