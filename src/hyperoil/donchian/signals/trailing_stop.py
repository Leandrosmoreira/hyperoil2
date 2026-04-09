"""Trailing stop that never recedes (long side).

The stop is anchored to the MID of the dominant Donchian channel — the
longest lookback that is currently breaking out. Once the stop has been
ratcheted up, it NEVER moves back down even if the dominant lookback
shrinks or the mid drops. This is the project rule, controlled by
``trailing_stop_never_recedes`` in DonchianRiskConfig.

Exit logic (handled by the decision engine, not here):
    - close <= trailing_stop  → emit EXIT
    - score < min_score_entry → emit EXIT (regime change)
    - portfolio drawdown > max_dd → emit EXIT for everything
"""

from __future__ import annotations


def update_trailing_stop(prev_stop: float | None, mid_dominant: float) -> float:
    """Ratchet a long-side trailing stop upward.

    - If ``prev_stop`` is None, the stop is being set for the first time
      (entry bar) → use ``mid_dominant`` directly.
    - Otherwise: ``max(prev_stop, mid_dominant)`` so the stop only goes UP.
    """
    if prev_stop is None:
        return mid_dominant
    return max(prev_stop, mid_dominant)
