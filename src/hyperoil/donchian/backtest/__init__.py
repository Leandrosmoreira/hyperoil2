"""Backtesting and optimization for Donchian strategy."""

from hyperoil.donchian.backtest.metrics import (
    DonchianMetrics,
    compute_donchian_metrics,
    format_donchian_report,
)
from hyperoil.donchian.backtest.multi_replay import (
    AssetBar,
    MultiAssetReplayEngine,
    MultiBar,
)
from hyperoil.donchian.backtest.simulator import (
    DonchianSimulator,
    SimulationResult,
    TradeRecord,
)

__all__ = [
    "AssetBar",
    "DonchianMetrics",
    "DonchianSimulator",
    "MultiAssetReplayEngine",
    "MultiBar",
    "SimulationResult",
    "TradeRecord",
    "compute_donchian_metrics",
    "format_donchian_report",
]
