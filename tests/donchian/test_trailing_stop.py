"""Unit tests for the never-receding trailing stop."""

from __future__ import annotations

from hyperoil.donchian.signals.trailing_stop import update_trailing_stop


def test_initial_stop_uses_mid_directly():
    assert update_trailing_stop(prev_stop=None, mid_dominant=100.0) == 100.0


def test_ratchet_up():
    s = update_trailing_stop(prev_stop=100.0, mid_dominant=110.0)
    assert s == 110.0


def test_never_recedes_when_mid_drops():
    """The CRITICAL invariant: even if mid_dominant drops, the stop holds."""
    s = update_trailing_stop(prev_stop=110.0, mid_dominant=95.0)
    assert s == 110.0


def test_equal_mid_and_prev_stays_put():
    s = update_trailing_stop(prev_stop=100.0, mid_dominant=100.0)
    assert s == 100.0


def test_long_sequence_only_goes_up():
    """Drive a sequence of mid values through the stop and assert monotonicity."""
    mids = [100.0, 105.0, 103.0, 110.0, 108.0, 115.0, 112.0, 116.0]
    stop = None
    history = []
    for m in mids:
        stop = update_trailing_stop(stop, m)
        history.append(stop)
    # Strictly non-decreasing
    for a, b in zip(history, history[1:]):
        assert b >= a
    # Final value is the running max
    assert history[-1] == max(mids)
