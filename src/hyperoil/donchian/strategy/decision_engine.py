"""Pure-function decision engine for the Donchian strategy.

Inputs (all immutable from the caller's POV):
    positions       current open positions, keyed by symbol
    signals         latest DonchianSignal per symbol (after trailing stops updated)
    targets         SizingResult per symbol (output of compute_portfolio_targets)
    prices          latest mark price per symbol (used for stop checks + execution)
    drawdown_pct    current portfolio drawdown (positive number)

Output: list[Decision], sorted so the orchestrator can execute strictly in
priority order:

    EXIT     (0) — de-risk first
    DECREASE (1) — free capital before adding
    INCREASE (2) — scale winners
    ENTER    (3) — only after de-risking
    HOLD     (4) — telemetry only, never executed

Exit triggers, in priority order:
    1. dd_shutdown    portfolio drawdown ≥ max_drawdown_pct
    2. stop_hit       price ≤ trailing_stop
    3. regime_change  score < min_score_entry
    4. size_zero      sizer collapsed the target to 0 (e.g. cap binding)

The engine is a pure function: it never mutates positions, signals, or
targets, and it does not call into the portfolio manager. The orchestrator
is responsible for materializing each Decision via PortfolioManager.
"""

from __future__ import annotations

from dataclasses import dataclass

from hyperoil.donchian.config import DonchianRiskConfig, DonchianSignalConfig, RiskParityConfig
from hyperoil.donchian.sizing.position_sizer import SizingResult
from hyperoil.donchian.types import DonchianAction, DonchianPosition, DonchianSignal

# Lower number = higher priority. Used for stable sort of the decision list.
_ACTION_PRIORITY: dict[DonchianAction, int] = {
    DonchianAction.EXIT: 0,
    DonchianAction.DECREASE: 1,
    DonchianAction.INCREASE: 2,
    DonchianAction.ENTER: 3,
    DonchianAction.HOLD: 4,
}


@dataclass(frozen=True)
class Decision:
    symbol: str
    action: DonchianAction
    reason: str
    price: float
    current_size_usd: float    # 0.0 if no open position
    target_size_usd: float     # 0.0 for EXIT and HOLD
    score: float
    leverage: float            # effective leverage from sizer (0 for EXIT)
    stop_line: float           # mid of dominant channel — used as initial stop on ENTER

    @property
    def priority(self) -> int:
        return _ACTION_PRIORITY[self.action]


class DonchianDecisionEngine:
    """Pure function that turns (state, signals, targets) into a sorted action list."""

    def __init__(
        self,
        signal_cfg: DonchianSignalConfig,
        risk_cfg: DonchianRiskConfig,
        risk_parity_cfg: RiskParityConfig,
    ) -> None:
        self.signal_cfg = signal_cfg
        self.risk_cfg = risk_cfg
        self.risk_parity_cfg = risk_parity_cfg

    def evaluate(
        self,
        positions: dict[str, DonchianPosition],
        signals: dict[str, DonchianSignal],
        targets: dict[str, SizingResult],
        prices: dict[str, float],
        drawdown_pct: float,
    ) -> list[Decision]:
        """Return the full action list for this bar, sorted by priority."""
        decisions: list[Decision] = []

        # 1) Walk every open position first — exits and rebalances of existing risk.
        for sym, pos in positions.items():
            decisions.append(
                self._evaluate_open(sym, pos, signals.get(sym), targets.get(sym),
                                    prices.get(sym, pos.current_price), drawdown_pct)
            )

        # 2) Walk targets for symbols WITHOUT an open position — pure entries.
        for sym, tgt in targets.items():
            if sym in positions:
                continue
            sig = signals.get(sym)
            price = prices.get(sym)
            decisions.append(self._evaluate_flat(sym, sig, tgt, price))

        # Stable sort by priority. EXIT first, ENTER last, HOLD trailing.
        decisions.sort(key=lambda d: d.priority)
        return decisions

    # ------------------------------------------------------------------
    # Open position branch — exit / decrease / increase / hold
    # ------------------------------------------------------------------
    def _evaluate_open(
        self,
        symbol: str,
        pos: DonchianPosition,
        sig: DonchianSignal | None,
        tgt: SizingResult | None,
        price: float,
        drawdown_pct: float,
    ) -> Decision:
        score = sig.score if sig is not None else pos.score_at_entry
        stop_line = sig.stop_line if sig is not None else pos.trailing_stop
        leverage = tgt.leverage_used if tgt is not None else pos.leverage

        # Exit priority 1: drawdown shutdown beats everything else.
        if drawdown_pct >= self.risk_cfg.max_drawdown_pct:
            return Decision(
                symbol=symbol, action=DonchianAction.EXIT, reason="dd_shutdown",
                price=price, current_size_usd=pos.size_usd, target_size_usd=0.0,
                score=score, leverage=0.0, stop_line=stop_line,
            )

        # Exit priority 2: trailing stop hit on the mark price.
        if price > 0 and pos.trailing_stop > 0 and price <= pos.trailing_stop:
            return Decision(
                symbol=symbol, action=DonchianAction.EXIT, reason="stop_hit",
                price=price, current_size_usd=pos.size_usd, target_size_usd=0.0,
                score=score, leverage=0.0, stop_line=stop_line,
            )

        # Exit priority 3: regime change — score collapsed below entry threshold.
        if sig is not None and sig.score < self.signal_cfg.min_score_entry:
            return Decision(
                symbol=symbol, action=DonchianAction.EXIT, reason="regime_change",
                price=price, current_size_usd=pos.size_usd, target_size_usd=0.0,
                score=score, leverage=0.0, stop_line=stop_line,
            )

        # Exit priority 4: sizer collapsed the target to 0 (cap binding, vol, etc).
        if tgt is not None and tgt.target_notional_usd <= 0.0:
            return Decision(
                symbol=symbol, action=DonchianAction.EXIT, reason="size_zero",
                price=price, current_size_usd=pos.size_usd, target_size_usd=0.0,
                score=score, leverage=0.0, stop_line=stop_line,
            )

        # No target this bar (e.g. signal not warm yet) → hold.
        if tgt is None or sig is None:
            return Decision(
                symbol=symbol, action=DonchianAction.HOLD, reason="no_signal",
                price=price, current_size_usd=pos.size_usd,
                target_size_usd=pos.size_usd, score=score, leverage=leverage,
                stop_line=stop_line,
            )

        # Rebalance check — only fire if the deviation exceeds the threshold.
        target = tgt.target_notional_usd
        threshold = self.risk_parity_cfg.rebal_threshold
        if pos.size_usd <= 0:
            # Defensive: a position with zero size shouldn't exist, but if it
            # does, treat any positive target as an INCREASE.
            delta_pct = float("inf") if target > 0 else 0.0
        else:
            delta_pct = (target - pos.size_usd) / pos.size_usd

        if delta_pct <= -threshold:
            return Decision(
                symbol=symbol, action=DonchianAction.DECREASE, reason="rebalance_down",
                price=price, current_size_usd=pos.size_usd, target_size_usd=target,
                score=score, leverage=leverage, stop_line=stop_line,
            )
        if delta_pct >= threshold:
            return Decision(
                symbol=symbol, action=DonchianAction.INCREASE, reason="rebalance_up",
                price=price, current_size_usd=pos.size_usd, target_size_usd=target,
                score=score, leverage=leverage, stop_line=stop_line,
            )
        return Decision(
            symbol=symbol, action=DonchianAction.HOLD, reason="within_band",
            price=price, current_size_usd=pos.size_usd, target_size_usd=target,
            score=score, leverage=leverage, stop_line=stop_line,
        )

    # ------------------------------------------------------------------
    # Flat branch — only ENTER or HOLD
    # ------------------------------------------------------------------
    def _evaluate_flat(
        self,
        symbol: str,
        sig: DonchianSignal | None,
        tgt: SizingResult,
        price: float | None,
    ) -> Decision:
        if sig is None or price is None or price <= 0:
            return Decision(
                symbol=symbol, action=DonchianAction.HOLD, reason="no_signal",
                price=price or 0.0, current_size_usd=0.0, target_size_usd=0.0,
                score=tgt.score, leverage=0.0, stop_line=0.0,
            )

        if tgt.target_notional_usd <= 0.0:
            return Decision(
                symbol=symbol, action=DonchianAction.HOLD, reason=f"target_zero:{tgt.cap_applied}",
                price=price, current_size_usd=0.0, target_size_usd=0.0,
                score=sig.score, leverage=0.0, stop_line=sig.stop_line,
            )

        if not sig.entry_valid:
            return Decision(
                symbol=symbol, action=DonchianAction.HOLD, reason="entry_invalid",
                price=price, current_size_usd=0.0, target_size_usd=0.0,
                score=sig.score, leverage=0.0, stop_line=sig.stop_line,
            )

        return Decision(
            symbol=symbol, action=DonchianAction.ENTER, reason="entry_valid",
            price=price, current_size_usd=0.0, target_size_usd=tgt.target_notional_usd,
            score=sig.score, leverage=tgt.leverage_used, stop_line=sig.stop_line,
        )
