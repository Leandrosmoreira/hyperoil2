"""Per-asset volatility targeting.

The portfolio targets a fixed annualized vol (default 25%). Each asset gets a
scaling factor based on its own realized vol vs the portfolio target::

    factor[i] = vol_target_annual / vol[i]

so an asset with realized vol equal to the target gets factor=1.0, a calmer
asset gets factor>1 (sized up) and a stormier asset gets factor<1 (sized
down). The factor is capped at `vol_factor_cap` (default 3.0) to prevent
runaway leverage on dead/low-vol series.

Why no `/n_assets` divisor: combined with risk-parity inverse-vol weights
that sum to 1.0, the portfolio's linear vol contribution is

    Σ w[i] · v[i] · factor[i] = Σ w[i] · vol_target = vol_target

i.e. dividing by n_assets here would shrink the realized portfolio vol by
1/N (with 25 assets, ~1% instead of the targeted 25%). The fix is to size
each asset to the full portfolio target — the diversification benefit shows
up in the realized (sub-linear) portfolio vol, not in the per-asset budget.
"""

from __future__ import annotations

import math

from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


class VolatilityTargetEngine:
    """Per-asset vol-target factor with a hard cap."""

    def __init__(
        self,
        vol_target_annual: float,
        vol_factor_cap: float = 3.0,
    ) -> None:
        if vol_target_annual <= 0.0:
            raise ValueError(f"vol_target_annual must be > 0, got {vol_target_annual}")
        if vol_factor_cap <= 0.0:
            raise ValueError(f"vol_factor_cap must be > 0, got {vol_factor_cap}")

        self.vol_target_annual = vol_target_annual
        self.vol_factor_cap = vol_factor_cap

    def factor(self, vol: float) -> float:
        """Vol-target factor for a single asset given its realized annualized vol.

        Returns 0.0 if `vol` is non-finite or non-positive (degenerate input —
        we cannot scale safely). Otherwise: capped at `vol_factor_cap`.
        """
        if vol is None or not math.isfinite(vol) or vol <= 0.0:
            log.warning("vol_target_skipped_symbol", vol=vol)
            return 0.0

        raw = self.vol_target_annual / vol
        return min(raw, self.vol_factor_cap)

    def factors(self, vols: dict[str, float]) -> dict[str, float]:
        return {sym: self.factor(v) for sym, v in vols.items()}
