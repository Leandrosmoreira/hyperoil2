"""Tests for the Donchian decision engine.

CRITICAL plan invariant: actions must be returned in the order
EXIT > DECREASE > INCREASE > ENTER (HOLD trails).
"""

from __future__ import annotations

import pytest

from hyperoil.donchian.config import (
    DonchianRiskConfig,
    DonchianSignalConfig,
    RiskParityConfig,
)
from hyperoil.donchian.sizing.position_sizer import SizingResult
from hyperoil.donchian.strategy.decision_engine import DonchianDecisionEngine
from hyperoil.donchian.types import DonchianAction, DonchianPosition, DonchianSignal


def _engine(rebal_threshold: float = 0.20) -> DonchianDecisionEngine:
    return DonchianDecisionEngine(
        signal_cfg=DonchianSignalConfig(),
        risk_cfg=DonchianRiskConfig(),
        risk_parity_cfg=RiskParityConfig(rebal_threshold=rebal_threshold),
    )


def _sig(symbol: str, score: float, stop: float = 90.0,
         ema: float = 80.0, valid: bool = True) -> DonchianSignal:
    return DonchianSignal(
        symbol=symbol, timestamp_ms=1, score=score,
        dominant_lookback=120, stop_line=stop, ema_200=ema, entry_valid=valid,
    )


def _tgt(symbol: str, target: float, score: float = 0.6,
         lev: float = 1.5, cap: str = "score") -> SizingResult:
    return SizingResult(
        symbol=symbol, target_notional_usd=target, sizing_factor=1.0,
        leverage_used=lev, weight=0.04, vol_factor=1.0, score=score, cap_applied=cap,
    )


def _pos(symbol: str, size: float = 1000.0, entry: float = 100.0,
         stop: float = 90.0) -> DonchianPosition:
    return DonchianPosition(
        symbol=symbol, side="long", entry_price=entry, current_price=entry,
        size_usd=size, leverage=1.5, trailing_stop=stop,
        score_at_entry=0.6, entry_timestamp_ms=0,
    )


# ----------------------------------------------------------------------
# Empty state
# ----------------------------------------------------------------------
def test_empty_universe_returns_empty():
    eng = _engine()
    assert eng.evaluate({}, {}, {}, {}, drawdown_pct=0.0) == []


# ----------------------------------------------------------------------
# Flat → ENTER / HOLD
# ----------------------------------------------------------------------
def test_flat_entry_valid_emits_enter():
    eng = _engine()
    decisions = eng.evaluate(
        positions={},
        signals={"BTC": _sig("BTC", 0.6)},
        targets={"BTC": _tgt("BTC", 600)},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert len(decisions) == 1
    assert decisions[0].action == DonchianAction.ENTER
    assert decisions[0].target_size_usd == 600
    assert decisions[0].stop_line == 90.0


def test_flat_entry_invalid_holds():
    eng = _engine()
    decisions = eng.evaluate(
        positions={},
        signals={"BTC": _sig("BTC", 0.6, valid=False)},
        targets={"BTC": _tgt("BTC", 600)},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.HOLD
    assert decisions[0].reason == "entry_invalid"


def test_flat_target_zero_holds():
    eng = _engine()
    decisions = eng.evaluate(
        positions={},
        signals={"BTC": _sig("BTC", 0.6)},
        targets={"BTC": _tgt("BTC", 0.0, cap="dd")},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.HOLD
    assert "target_zero" in decisions[0].reason


def test_flat_no_signal_holds():
    eng = _engine()
    decisions = eng.evaluate(
        positions={}, signals={}, targets={"BTC": _tgt("BTC", 600)},
        prices={"BTC": 100.0}, drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.HOLD


# ----------------------------------------------------------------------
# Open → EXIT triggers
# ----------------------------------------------------------------------
def test_dd_shutdown_beats_everything():
    eng = _engine()
    decisions = eng.evaluate(
        positions={"BTC": _pos("BTC")},
        signals={"BTC": _sig("BTC", 0.9)},
        targets={"BTC": _tgt("BTC", 5000)},  # Would normally INCREASE
        prices={"BTC": 200.0},
        drawdown_pct=0.25,
    )
    assert decisions[0].action == DonchianAction.EXIT
    assert decisions[0].reason == "dd_shutdown"


def test_stop_hit_emits_exit():
    eng = _engine()
    decisions = eng.evaluate(
        positions={"BTC": _pos("BTC", stop=95.0)},
        signals={"BTC": _sig("BTC", 0.9)},
        targets={"BTC": _tgt("BTC", 1000)},
        prices={"BTC": 90.0},  # Below stop
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.EXIT
    assert decisions[0].reason == "stop_hit"


def test_regime_change_exits():
    eng = _engine()
    decisions = eng.evaluate(
        positions={"BTC": _pos("BTC")},
        signals={"BTC": _sig("BTC", 0.10)},  # Below 0.33
        targets={"BTC": _tgt("BTC", 0.0, cap="score")},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.EXIT
    assert decisions[0].reason == "regime_change"


def test_size_zero_exits():
    eng = _engine()
    decisions = eng.evaluate(
        positions={"BTC": _pos("BTC")},
        signals={"BTC": _sig("BTC", 0.6)},
        targets={"BTC": _tgt("BTC", 0.0, cap="vol")},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.EXIT
    assert decisions[0].reason == "size_zero"


# ----------------------------------------------------------------------
# Open → INCREASE / DECREASE / HOLD via rebal threshold
# ----------------------------------------------------------------------
def test_rebalance_up_above_threshold():
    eng = _engine(rebal_threshold=0.20)
    # Current 1000 → target 1300 = +30%
    decisions = eng.evaluate(
        positions={"BTC": _pos("BTC", size=1000)},
        signals={"BTC": _sig("BTC", 0.6)},
        targets={"BTC": _tgt("BTC", 1300)},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.INCREASE
    assert decisions[0].target_size_usd == 1300


def test_rebalance_down_above_threshold():
    eng = _engine(rebal_threshold=0.20)
    # Current 1000 → target 700 = -30%
    decisions = eng.evaluate(
        positions={"BTC": _pos("BTC", size=1000)},
        signals={"BTC": _sig("BTC", 0.6)},
        targets={"BTC": _tgt("BTC", 700)},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.DECREASE


def test_within_rebalance_band_holds():
    eng = _engine(rebal_threshold=0.20)
    # Current 1000 → target 1100 = +10% (under 20% threshold)
    decisions = eng.evaluate(
        positions={"BTC": _pos("BTC", size=1000)},
        signals={"BTC": _sig("BTC", 0.6)},
        targets={"BTC": _tgt("BTC", 1100)},
        prices={"BTC": 100.0},
        drawdown_pct=0.0,
    )
    assert decisions[0].action == DonchianAction.HOLD
    assert decisions[0].reason == "within_band"


# ----------------------------------------------------------------------
# CRITICAL: priority ordering EXIT > DECREASE > INCREASE > ENTER
# ----------------------------------------------------------------------
def test_action_priority_ordering():
    """Mix of all four actions must come back in EXIT > DECREASE > INCREASE > ENTER order."""
    eng = _engine(rebal_threshold=0.20)

    positions = {
        "EXIT_SYM": _pos("EXIT_SYM", size=1000, stop=95.0),       # Will EXIT (stop hit)
        "DEC_SYM":  _pos("DEC_SYM",  size=1000),                   # Will DECREASE
        "INC_SYM":  _pos("INC_SYM",  size=1000),                   # Will INCREASE
    }
    signals = {
        "EXIT_SYM": _sig("EXIT_SYM", 0.6),
        "DEC_SYM":  _sig("DEC_SYM",  0.6),
        "INC_SYM":  _sig("INC_SYM",  0.6),
        "ENT_SYM":  _sig("ENT_SYM",  0.6),
    }
    targets = {
        "EXIT_SYM": _tgt("EXIT_SYM", 1000),
        "DEC_SYM":  _tgt("DEC_SYM",  500),    # -50%
        "INC_SYM":  _tgt("INC_SYM",  1500),   # +50%
        "ENT_SYM":  _tgt("ENT_SYM",  600),    # New entry
    }
    prices = {
        "EXIT_SYM": 90.0,    # Below the 95 stop
        "DEC_SYM":  100.0,
        "INC_SYM":  100.0,
        "ENT_SYM":  100.0,
    }

    decisions = eng.evaluate(positions, signals, targets, prices, drawdown_pct=0.0)
    actions = [d.action for d in decisions]
    assert actions == [
        DonchianAction.EXIT,
        DonchianAction.DECREASE,
        DonchianAction.INCREASE,
        DonchianAction.ENTER,
    ]


def test_multiple_exits_all_first():
    """Multiple EXITs followed by an ENTER — every EXIT must precede the ENTER."""
    eng = _engine()
    positions = {
        "A": _pos("A", stop=95.0),
        "B": _pos("B", stop=95.0),
    }
    signals = {
        "A": _sig("A", 0.6), "B": _sig("B", 0.6), "C": _sig("C", 0.6),
    }
    targets = {
        "A": _tgt("A", 1000), "B": _tgt("B", 1000), "C": _tgt("C", 600),
    }
    prices = {"A": 80.0, "B": 80.0, "C": 100.0}
    decisions = eng.evaluate(positions, signals, targets, prices, drawdown_pct=0.0)

    exit_idx = [i for i, d in enumerate(decisions) if d.action == DonchianAction.EXIT]
    enter_idx = [i for i, d in enumerate(decisions) if d.action == DonchianAction.ENTER]
    assert len(exit_idx) == 2
    assert len(enter_idx) == 1
    assert max(exit_idx) < min(enter_idx)


def test_engine_does_not_mutate_inputs():
    """Pure-function contract: positions/signals/targets must not be touched."""
    eng = _engine()
    positions = {"BTC": _pos("BTC")}
    signals = {"BTC": _sig("BTC", 0.6)}
    targets = {"BTC": _tgt("BTC", 1500)}

    snap_pos_size = positions["BTC"].size_usd
    snap_target = targets["BTC"].target_notional_usd

    eng.evaluate(positions, signals, targets, {"BTC": 100.0}, drawdown_pct=0.0)

    assert positions["BTC"].size_usd == snap_pos_size
    assert targets["BTC"].target_notional_usd == snap_target
