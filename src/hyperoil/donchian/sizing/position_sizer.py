"""Per-position notional sizer combining all leverage caps.

Inputs (per asset):
    - capital              portfolio equity in USD
    - weight               from RiskParityEngine, sums to 1.0 across the universe
    - vol_factor           from VolatilityTargetEngine, capped at vol_factor_cap
    - score                from the Donchian ensemble, 0.0 - 1.0
    - asset_class          for the per-class leverage cap
    - api_max_leverage     from Hyperliquid HIP-3 metadata
    - drawdown_pct         current portfolio drawdown (positive number)

Score → (sizing_factor, leverage_multiplier):
    score < 0.33   → (0.0, 0.0)   no position at all
    0.33–0.54      → (0.5, 1.0)
    0.55–0.69      → (1.0, 1.5)
    0.70–0.84      → (1.0, 2.0)
    score ≥ 0.85   → (1.0, 3.0)

Effective leverage = min(score_lev, dd_cap, class_cap, api_max).
The cap that actually bound the leverage is reported in `cap_applied` for
telemetry — useful to detect when a regime is being clipped by drawdown vs
exchange limits vs the score itself.

Drawdown caps (on the leverage multiplier, not the sizing factor):
    dd ≥ 20%   → 0          (close everything)
    dd ≥ 15%   → ≤ 1.0×
    dd ≥ 10%   → ≤ 1.5×
    dd <  10%  → no cap

Final notional:
    target_usd = capital × weight × vol_factor × sizing_factor × effective_leverage

…then clipped to `capital × max_position_pct` (default 40%) as a final hard
ceiling regardless of how aggressive the score / leverage stack got.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from hyperoil.donchian.config import DonchianRiskConfig, DonchianSizingConfig
from hyperoil.donchian.sizing.risk_parity import RiskParityEngine
from hyperoil.donchian.sizing.vol_target import VolatilityTargetEngine
from hyperoil.donchian.types import CLASS_MAX_LEVERAGE, AssetClass


@dataclass(frozen=True)
class SizingResult:
    symbol: str
    target_notional_usd: float
    sizing_factor: float        # 0.0 / 0.5 / 1.0 from score tier
    leverage_used: float        # effective leverage after all caps
    weight: float               # risk-parity weight (informational)
    vol_factor: float           # vol-target factor (informational)
    score: float
    cap_applied: str            # "score" / "dd" / "class" / "api" / "max_pos_pct" / "none"


def compute_score_tier(
    score: float,
    thresholds: dict[str, float],
) -> tuple[float, float]:
    """Map a score to (sizing_factor, leverage_multiplier).

    `thresholds` is the dict from DonchianSizingConfig.score_thresholds:
        half_pos    → 0.33   (below: no position)
        full_pos    → 0.55   (full size, lev 1.0–1.5)
        lever_2x    → 0.70
        lever_3x    → 0.85
    """
    half = thresholds["half_pos"]
    full = thresholds["full_pos"]
    lev2 = thresholds["lever_2x"]
    lev3 = thresholds["lever_3x"]

    if score < half:
        return (0.0, 0.0)
    if score < full:
        return (0.5, 1.0)
    if score < lev2:
        return (1.0, 1.5)
    if score < lev3:
        return (1.0, 2.0)
    return (1.0, 3.0)


def compute_drawdown_cap(dd_pct: float, risk_cfg: DonchianRiskConfig) -> float:
    """Max allowed leverage multiplier given the current portfolio drawdown.

    `dd_pct` is positive (0.10 == 10% drawdown). Returns 0.0 to mean
    "shut down completely", inf to mean "no cap".
    """
    if dd_pct >= risk_cfg.max_drawdown_pct:
        return 0.0
    if dd_pct >= risk_cfg.dd_reduce_15_pct:
        return 1.0
    if dd_pct >= risk_cfg.dd_reduce_10_pct:
        return 1.5
    return float("inf")


class DonchianPositionSizer:
    """Combines risk parity, vol targeting, score tier and all leverage caps."""

    def __init__(
        self,
        sizing_cfg: DonchianSizingConfig,
        risk_cfg: DonchianRiskConfig,
    ) -> None:
        self.sizing_cfg = sizing_cfg
        self.risk_cfg = risk_cfg

    def size_position(
        self,
        symbol: str,
        capital: float,
        weight: float,
        vol_factor: float,
        score: float,
        asset_class: AssetClass,
        api_max_leverage: float,
        drawdown_pct: float,
    ) -> SizingResult:
        sizing_factor, score_lev = compute_score_tier(
            score, self.sizing_cfg.score_thresholds
        )

        # Score below entry threshold → flat
        if sizing_factor == 0.0:
            return SizingResult(
                symbol=symbol,
                target_notional_usd=0.0,
                sizing_factor=0.0,
                leverage_used=0.0,
                weight=weight,
                vol_factor=vol_factor,
                score=score,
                cap_applied="score",
            )

        # Drawdown shutdown
        dd_cap = compute_drawdown_cap(drawdown_pct, self.risk_cfg)
        if dd_cap == 0.0:
            return SizingResult(
                symbol=symbol,
                target_notional_usd=0.0,
                sizing_factor=0.0,
                leverage_used=0.0,
                weight=weight,
                vol_factor=vol_factor,
                score=score,
                cap_applied="dd",
            )

        class_cap = CLASS_MAX_LEVERAGE.get(asset_class, float("inf"))
        api_cap = api_max_leverage if math.isfinite(api_max_leverage) else float("inf")

        candidates = {
            "score": score_lev,
            "dd": dd_cap,
            "class": class_cap,
            "api": api_cap,
        }
        # The binding cap is the smallest one
        cap_applied, effective = min(candidates.items(), key=lambda kv: kv[1])

        target = capital * weight * vol_factor * sizing_factor * effective

        # Final hard ceiling: max position pct of capital
        max_notional = capital * self.sizing_cfg.max_position_pct
        if target > max_notional:
            target = max_notional
            cap_applied = "max_pos_pct"

        return SizingResult(
            symbol=symbol,
            target_notional_usd=target,
            sizing_factor=sizing_factor,
            leverage_used=effective,
            weight=weight,
            vol_factor=vol_factor,
            score=score,
            cap_applied=cap_applied,
        )


def compute_portfolio_targets(
    *,
    vols: dict[str, float],
    scores: dict[str, float],
    asset_classes: dict[str, AssetClass],
    api_max_leverage: dict[str, float],
    capital: float,
    drawdown_pct: float,
    risk_parity: RiskParityEngine,
    vol_target: VolatilityTargetEngine,
    sizer: DonchianPositionSizer,
) -> dict[str, SizingResult]:
    """End-to-end: vols → weights → factors → per-symbol targets.

    `scores` keys are the universe of symbols to size. `vols` MUST contain a
    valid number for every symbol you want sized; degenerate vols are
    silently dropped (the symbol gets a zero target with cap_applied='vol').
    """
    weights = risk_parity.compute_weights(vols)

    out: dict[str, SizingResult] = {}
    for sym, score in scores.items():
        if sym not in weights:
            out[sym] = SizingResult(
                symbol=sym,
                target_notional_usd=0.0,
                sizing_factor=0.0,
                leverage_used=0.0,
                weight=0.0,
                vol_factor=0.0,
                score=score,
                cap_applied="vol",
            )
            continue

        out[sym] = sizer.size_position(
            symbol=sym,
            capital=capital,
            weight=weights[sym],
            vol_factor=vol_target.factor(vols[sym]),
            score=score,
            asset_class=asset_classes[sym],
            api_max_leverage=api_max_leverage.get(sym, float("inf")),
            drawdown_pct=drawdown_pct,
        )

    return out
