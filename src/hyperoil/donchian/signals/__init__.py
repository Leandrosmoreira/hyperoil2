"""Signal generation for Donchian Ensemble strategy."""

from hyperoil.donchian.signals.donchian_channel import Channel, compute_channel
from hyperoil.donchian.signals.ensemble import EnsembleResult, compute_ensemble
from hyperoil.donchian.signals.regime_ema import compute_ema, entry_allowed
from hyperoil.donchian.signals.signal_engine import DonchianSignalEngine
from hyperoil.donchian.signals.trailing_stop import update_trailing_stop

__all__ = [
    "Channel",
    "DonchianSignalEngine",
    "EnsembleResult",
    "compute_channel",
    "compute_ema",
    "compute_ensemble",
    "entry_allowed",
    "update_trailing_stop",
]
