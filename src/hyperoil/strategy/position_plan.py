"""Position sizing and hedge calculation for grid levels."""

from __future__ import annotations

from dataclasses import dataclass

from hyperoil.config import GridConfig, SizingConfig
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class LegSizes:
    """Computed sizes for both legs of a pair trade."""
    size_left: float   # notional in base units for left leg (CL)
    size_right: float  # notional in base units for right leg (BRENTOIL)
    notional_usd: float  # total notional USD (both legs)


class PositionPlanner:
    """Computes position sizes for grid levels, respecting limits."""

    def __init__(self, sizing: SizingConfig, grid: GridConfig) -> None:
        self._sizing = sizing
        self._grid = grid

    def compute_sizes(
        self,
        level: int,
        beta: float,
        price_left: float,
        price_right: float,
        existing_notional: float = 0.0,
    ) -> LegSizes | None:
        """Compute leg sizes for a grid level entry.

        Args:
            level: Grid level (1-indexed)
            beta: Current hedge ratio
            price_left: Current price of left leg (CL)
            price_right: Current price of right leg (BRENTOIL)
            existing_notional: Total notional already deployed in this cycle

        Returns:
            LegSizes or None if limits would be exceeded.
        """
        if price_left <= 0 or price_right <= 0 or beta <= 0:
            log.warning(
                "position_plan_invalid_inputs",
                price_left=price_left,
                price_right=price_right,
                beta=beta,
            )
            return None

        # Get multiplier for this level
        mult = self._get_level_mult(level)
        base = self._sizing.base_notional_usd * mult

        # Compute leg sizes based on hedge mode
        if self._sizing.hedge_mode == "beta_adjusted":
            # Left leg gets base notional, right leg adjusted by beta
            size_left = base / price_left
            size_right = (base * beta) / price_right
        else:
            # Equal notional on both legs
            size_left = base / price_left
            size_right = base / price_right

        notional_this = base + (base * beta if self._sizing.hedge_mode == "beta_adjusted" else base)

        # Check per-cycle limit
        if existing_notional + notional_this > self._sizing.max_notional_per_cycle:
            log.info(
                "position_plan_cycle_limit_hit",
                existing=existing_notional,
                requested=notional_this,
                limit=self._sizing.max_notional_per_cycle,
            )
            return None

        # Check total limit
        if existing_notional + notional_this > self._sizing.max_total_notional:
            log.info(
                "position_plan_total_limit_hit",
                existing=existing_notional,
                requested=notional_this,
                limit=self._sizing.max_total_notional,
            )
            return None

        return LegSizes(
            size_left=round(size_left, 6),
            size_right=round(size_right, 6),
            notional_usd=round(notional_this, 2),
        )

    def _get_level_mult(self, level: int) -> float:
        """Get size multiplier for a grid level (1-indexed)."""
        idx = level - 1
        if 0 <= idx < len(self._grid.levels):
            return self._grid.levels[idx].mult
        return 1.0

    def compute_exit_sizes(
        self,
        size_left: float,
        size_right: float,
        fraction: float = 1.0,
    ) -> LegSizes:
        """Compute exit sizes (proportional reduction of both legs).

        Args:
            size_left, size_right: Current position sizes
            fraction: 1.0 = full exit, 0.5 = half exit
        """
        fraction = max(0.0, min(1.0, fraction))
        return LegSizes(
            size_left=round(size_left * fraction, 6),
            size_right=round(size_right * fraction, 6),
            notional_usd=0.0,  # not tracked on exits
        )
