"""Integration-ish tests for the multi-asset Donchian signal engine."""

from __future__ import annotations

import numpy as np

from hyperoil.donchian.signals.signal_engine import DonchianSignalEngine


def _bar(ts: int, o: float, h: float, l: float, c: float) -> dict:
    return {"timestamp_ms": ts, "open": o, "high": h, "low": l, "close": c}


def _make_series(n: int, base: float = 100.0) -> list[dict]:
    """Generate `n` bars with mild noise around `base`, 4h spacing."""
    rng = np.random.default_rng(0)
    closes = base + rng.normal(0, 0.5, size=n).cumsum() * 0.0  # flat baseline
    closes = np.full(n, base) + rng.normal(0, 0.1, size=n)
    bars = []
    for i, c in enumerate(closes):
        ts = i * 4 * 3600 * 1000
        bars.append(_bar(ts, float(c), float(c) + 0.2, float(c) - 0.2, float(c)))
    return bars


def test_warmup_returns_none_until_enough_bars():
    eng = DonchianSignalEngine(lookbacks=[5, 10], ema_period=20)
    eng.seed_history("BTC", _make_series(5))   # less than max+1
    assert eng.is_warm("BTC") is False
    assert eng.compute_signal("BTC") is None


def test_warm_after_max_lookback_plus_one():
    eng = DonchianSignalEngine(lookbacks=[5, 10], ema_period=20)
    eng.seed_history("BTC", _make_series(11))  # exactly max(10) + 1
    assert eng.is_warm("BTC") is True
    sig = eng.compute_signal("BTC")
    assert sig is not None
    assert sig.symbol == "BTC"
    assert 0.0 <= sig.score <= 1.0
    assert len(sig.channels) == 2


def test_breakout_score_one_on_synthetic_spike():
    """Flat history followed by a huge upward close → score=1.0, entry valid."""
    eng = DonchianSignalEngine(
        lookbacks=[5, 10, 20],
        ema_period=20,
        min_score_entry=0.33,
    )
    bars = _make_series(50, base=100.0)
    # Replace the last bar with a clear breakout
    last_ts = bars[-1]["timestamp_ms"]
    bars[-1] = _bar(last_ts, 100.0, 200.0, 100.0, 195.0)
    eng.seed_history("BTC", bars)
    sig = eng.compute_signal("BTC")
    assert sig is not None
    assert sig.score == 1.0
    assert sig.dominant_lookback == 20  # largest lookback that broke out
    assert sig.entry_valid is True
    # Stop line is the mid of the dominant channel
    dom_ch = next(c for c in sig.channels if c.lookback == 20)
    assert sig.stop_line == dom_ch.mid


def test_no_breakout_score_zero_entry_invalid():
    eng = DonchianSignalEngine(lookbacks=[5, 10, 20], ema_period=20)
    bars = _make_series(50, base=100.0)
    # Force the last close to be DEEP below the prior 20-bar low
    last_ts = bars[-1]["timestamp_ms"]
    bars[-1] = _bar(last_ts, 100.0, 100.1, 50.0, 50.0)
    eng.seed_history("BTC", bars)
    sig = eng.compute_signal("BTC")
    assert sig is not None
    assert sig.score == 0.0
    assert sig.dominant_lookback == 0
    # close < EMA → entry invalid even if score were > 0
    assert sig.entry_valid is False


def test_update_bar_extends_buffer_and_drops_old():
    eng = DonchianSignalEngine(lookbacks=[5], ema_period=10, buffer_size=10)
    eng.seed_history("BTC", _make_series(10))
    assert eng.n_bars("BTC") == 10
    eng.update_bar("BTC", _bar(99999, 1.0, 2.0, 0.5, 1.5))
    # Bounded deque drops the oldest
    assert eng.n_bars("BTC") == 10
    last = list(eng._buffers["BTC"])[-1]
    assert last.timestamp_ms == 99999


def test_compute_all_skips_unwarm_symbols():
    eng = DonchianSignalEngine(lookbacks=[5, 10], ema_period=20)
    eng.seed_history("BTC", _make_series(20))   # warm
    eng.seed_history("ETH", _make_series(5))    # NOT warm
    out = eng.compute_all()
    assert "BTC" in out
    assert "ETH" not in out


def test_multi_asset_independence():
    """Two symbols should not interfere with each other's signals."""
    eng = DonchianSignalEngine(lookbacks=[5, 10], ema_period=20)
    btc = _make_series(50, base=100.0)
    eth = _make_series(50, base=200.0)
    # Spike only BTC
    btc[-1] = _bar(btc[-1]["timestamp_ms"], 100.0, 300.0, 100.0, 290.0)
    eng.seed_history("BTC", btc)
    eng.seed_history("ETH", eth)
    out = eng.compute_all()
    assert out["BTC"].score == 1.0
    assert out["ETH"].score == 0.0
    # ETH stop_line is independent of BTC's spike
    assert abs(out["ETH"].stop_line - 200.0) < 5.0
