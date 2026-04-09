"""End-to-end Donchian backtest simulator.

Wires together all the Sprint 1-4 modules to run a deterministic, bar-by-bar
replay of the strategy on historical data:

    MultiAssetReplayEngine  → aligned 4h bars for the universe
    DonchianSignalEngine    → ensemble score per asset
    RiskParityEngine        → inverse-vol weights
    VolatilityTargetEngine  → vol-target factors
    DonchianPositionSizer   → per-asset target notional
    DonchianDecisionEngine  → EXIT > DECREASE > INCREASE > ENTER actions
    PortfolioManager        → applies the decisions, marks to market, tracks DD

Outputs a SimulationResult with the equity curve, the trade ledger, and the
final portfolio snapshot. The metrics module turns that into a report.

Determinism guarantees:
    1. Bars are iterated in timestamp order from the aligned grid.
    2. compute_signal uses only bars STRICTLY BEFORE the current bar (the
       Donchian channel module already enforces this — see test_donchian_channel).
    3. Vols are computed from CLOSED-ONLY history (no peek at the current bar).
    4. Decisions are applied at the close of the bar that produced them.
    5. Fees are deducted at the model close — slippage is approximated by a
       small bps add-on to the execution price.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from hyperoil.donchian.backtest.multi_replay import MultiAssetReplayEngine, MultiBar
from hyperoil.donchian.config import DonchianAppConfig
from hyperoil.donchian.signals.signal_engine import DonchianSignalEngine
from hyperoil.donchian.sizing.position_sizer import (
    DonchianPositionSizer,
    compute_portfolio_targets,
)
from hyperoil.donchian.sizing.risk_parity import RiskParityEngine, compute_realized_vol
from hyperoil.donchian.sizing.vol_target import VolatilityTargetEngine
from hyperoil.donchian.strategy.decision_engine import DonchianDecisionEngine
from hyperoil.donchian.strategy.portfolio_manager import PortfolioManager
from hyperoil.donchian.types import (
    LOOKBACKS_4H,
    AssetClass,
    DonchianAction,
)
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    """One executed action in the backtest."""
    timestamp_ms: int
    symbol: str
    action: str
    price: float
    size_usd_before: float
    size_usd_after: float
    realized_pnl: float
    fee: float
    score: float
    leverage: float
    reason: str


@dataclass
class SimulationResult:
    initial_capital: float
    final_equity: float
    n_bars: int
    n_warmup_bars: int
    n_trades: int
    equity_curve: list[float] = field(default_factory=list)
    timestamps: list[int] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    per_asset_pnl: dict[str, float] = field(default_factory=dict)
    per_asset_trades: dict[str, int] = field(default_factory=dict)


def _load_api_max_leverage(path: str = "config/ticker_mapping.json") -> dict[str, float]:
    """Read max leverage per dex_symbol from the Sprint 0 ticker map.

    Falls back to ``inf`` (no API cap) for any asset missing from the file.
    """
    p = Path(path)
    if not p.exists():
        log.warning("ticker_mapping_missing", path=path)
        return {}
    raw = json.loads(p.read_text())
    out: dict[str, float] = {}
    for _, info in raw.get("ticker_mapping", {}).items():
        sym = info.get("dex_symbol")
        lev = info.get("max_leverage")
        if sym and lev is not None:
            out[sym] = float(lev)
    return out


class DonchianSimulator:
    """Deterministic backtest runner for the Donchian Ensemble strategy."""

    def __init__(
        self,
        cfg: DonchianAppConfig,
        replay: MultiAssetReplayEngine,
        api_max_leverage: dict[str, float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.replay = replay
        self.api_max_leverage = api_max_leverage or _load_api_max_leverage()

        # Build the asset_class lookup keyed by dex_symbol (the canonical name
        # used by both the replay engine and the portfolio manager).
        self.asset_classes: dict[str, AssetClass] = {}
        for a in cfg.universe.assets:
            self.asset_classes[f"{a.dex_prefix}:{a.hl_ticker}"] = a.asset_class

        # --- Engines ---
        self.signal_engine = DonchianSignalEngine(
            lookbacks=cfg.signal.lookbacks,
            ema_period=cfg.signal.ema_period,
            min_score_entry=cfg.signal.min_score_entry,
        )
        self.risk_parity = RiskParityEngine()
        self.vol_target = VolatilityTargetEngine(
            vol_target_annual=cfg.sizing.vol_target_annual,
            vol_factor_cap=cfg.sizing.vol_factor_cap,
        )
        self.sizer = DonchianPositionSizer(sizing_cfg=cfg.sizing, risk_cfg=cfg.risk)
        self.decision_engine = DonchianDecisionEngine(
            signal_cfg=cfg.signal,
            risk_cfg=cfg.risk,
            risk_parity_cfg=cfg.risk_parity,
        )
        self.portfolio = PortfolioManager(
            initial_capital=cfg.backtest.initial_capital,
            fee_maker_bps=cfg.backtest.fee_maker_bps,
            fee_taker_bps=cfg.backtest.fee_taker_bps,
            slippage_bps=cfg.backtest.slippage_bps,
        )

        self.warmup_bars = max(cfg.signal.lookbacks) + 1
        # Vol window in 4h bars: vol_window is in DAYS in the config (the
        # paper convention) so we multiply by 6 (4h candles per day).
        self.vol_window_4h = max(2, cfg.risk_parity.vol_window * 6)

        self._closes_buffer: dict[str, list[float]] = {s: [] for s in replay.symbols}

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------
    def run(self) -> SimulationResult:
        result = SimulationResult(
            initial_capital=self.portfolio.equity,
            final_equity=self.portfolio.equity,
            n_bars=0,
            n_warmup_bars=self.warmup_bars,
            n_trades=0,
        )

        n_warm_done = 0
        for i, mbar in enumerate(self.replay.iter_bars()):
            self._absorb_bar(mbar)

            # Warmup: keep filling buffers until every symbol can produce a
            # full-history signal. Decisions and equity tracking start AFTER.
            if not self._all_warm():
                n_warm_done += 1
                continue

            self._process_bar(mbar, result)

        # Force-close everything on the final bar at the last close price.
        if mbar is not None:
            self._final_close(mbar, result)

        result.n_bars = i + 1 - n_warm_done
        result.n_warmup_bars = n_warm_done
        result.final_equity = self.portfolio.equity
        result.n_trades = len(result.trades)
        log.info(
            "simulation_complete",
            n_bars=result.n_bars,
            n_warmup=result.n_warmup_bars,
            n_trades=result.n_trades,
            final_equity=round(result.final_equity, 2),
            ret_pct=round((result.final_equity / result.initial_capital - 1) * 100, 2),
        )
        return result

    # ------------------------------------------------------------------
    # Per-bar pipeline
    # ------------------------------------------------------------------
    def _absorb_bar(self, mbar: MultiBar) -> None:
        """Push the bar's OHLC into the signal engine and the closes buffer."""
        for sym, bar in mbar.bars.items():
            self.signal_engine.update_bar(
                sym,
                {
                    "timestamp_ms": mbar.timestamp_ms,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                },
            )
            self._closes_buffer[sym].append(bar.close)
            # Cap the closes buffer at vol_window_4h + headroom to keep memory bounded.
            if len(self._closes_buffer[sym]) > self.vol_window_4h + 50:
                self._closes_buffer[sym] = self._closes_buffer[sym][-(self.vol_window_4h + 50):]

    def _all_warm(self) -> bool:
        return all(self.signal_engine.is_warm(s) for s in self.replay.symbols)

    def _process_bar(self, mbar: MultiBar, result: SimulationResult) -> None:
        ts = mbar.timestamp_ms
        prices = {s: b.close for s, b in mbar.bars.items()}

        # 1) Mark to market with the new closes.
        self.portfolio.update_prices(prices, timestamp_ms=ts)

        # 2) Recompute signals for the universe.
        signals = self.signal_engine.compute_all()

        # 3) Ratchet trailing stops on every open position.
        self.portfolio.update_trailing_stops(signals)

        # 4) Compute realized vol → weights → vol factor → per-asset targets.
        vols: dict[str, float] = {}
        for sym in self.replay.symbols:
            v = compute_realized_vol(
                np.asarray(self._closes_buffer[sym], dtype=float),
                window=self.vol_window_4h,
            )
            if math.isfinite(v):
                vols[sym] = float(v)

        scores = {sym: sig.score for sym, sig in signals.items()}
        targets = compute_portfolio_targets(
            vols=vols,
            scores=scores,
            asset_classes=self.asset_classes,
            api_max_leverage=self.api_max_leverage,
            capital=self.portfolio.equity,
            drawdown_pct=self.portfolio.drawdown_pct,
            risk_parity=self.risk_parity,
            vol_target=self.vol_target,
            sizer=self.sizer,
        )

        # 5) Decide.
        decisions = self.decision_engine.evaluate(
            positions=self.portfolio.positions,
            signals=signals,
            targets=targets,
            prices=prices,
            drawdown_pct=self.portfolio.drawdown_pct,
        )

        # 6) Execute the decisions in priority order (already sorted).
        for d in decisions:
            self._apply_decision(d, ts, result)

        # 7) Equity curve point.
        result.equity_curve.append(self.portfolio.equity)
        result.timestamps.append(ts)
        result.drawdown_curve.append(self.portfolio.drawdown_pct)

    # ------------------------------------------------------------------
    # Apply one Decision against the portfolio
    # ------------------------------------------------------------------
    def _apply_decision(self, d, ts: int, result: SimulationResult) -> None:
        sym = d.symbol
        price = d.price if d.price > 0 else None
        if price is None:
            return

        before = (
            self.portfolio.get_position(sym).size_usd
            if self.portfolio.get_position(sym) is not None
            else 0.0
        )

        cash_before = self.portfolio.cash
        realized_before = self.portfolio.state.realized_pnl_total
        fees_before = self.portfolio.state.fees_paid_total

        if d.action == DonchianAction.ENTER:
            self.portfolio.open_position(
                symbol=sym,
                size_usd=d.target_size_usd,
                price=price,
                score=d.score,
                leverage=d.leverage,
                stop_line=d.stop_line,
                timestamp_ms=ts,
                is_maker=True,
            )
        elif d.action == DonchianAction.INCREASE:
            self.portfolio.increase_position(
                symbol=sym,
                new_size_usd=d.target_size_usd,
                price=price,
                timestamp_ms=ts,
                is_maker=True,
            )
        elif d.action == DonchianAction.DECREASE:
            self.portfolio.decrease_position(
                symbol=sym,
                new_size_usd=d.target_size_usd,
                price=price,
                timestamp_ms=ts,
                is_maker=True,
            )
        elif d.action == DonchianAction.EXIT:
            self.portfolio.close_position(
                symbol=sym,
                price=price,
                timestamp_ms=ts,
                is_maker=(d.reason != "stop_hit"),
                reason=d.reason,
            )
        else:
            return  # HOLD — nothing to do.

        after_pos = self.portfolio.get_position(sym)
        after = after_pos.size_usd if after_pos is not None else 0.0
        realized_delta = self.portfolio.state.realized_pnl_total - realized_before
        fee_delta = self.portfolio.state.fees_paid_total - fees_before

        result.trades.append(
            TradeRecord(
                timestamp_ms=ts,
                symbol=sym,
                action=d.action.value,
                price=price,
                size_usd_before=before,
                size_usd_after=after,
                realized_pnl=realized_delta,
                fee=fee_delta,
                score=d.score,
                leverage=d.leverage,
                reason=d.reason,
            )
        )
        result.per_asset_pnl[sym] = result.per_asset_pnl.get(sym, 0.0) + realized_delta
        result.per_asset_trades[sym] = result.per_asset_trades.get(sym, 0) + 1

    # ------------------------------------------------------------------
    # End-of-run cleanup
    # ------------------------------------------------------------------
    def _final_close(self, mbar: MultiBar, result: SimulationResult) -> None:
        ts = mbar.timestamp_ms
        for sym in list(self.portfolio.positions.keys()):
            price = mbar.bars[sym].close
            before = self.portfolio.positions[sym].size_usd
            realized_before = self.portfolio.state.realized_pnl_total
            fees_before = self.portfolio.state.fees_paid_total
            self.portfolio.close_position(
                symbol=sym, price=price, timestamp_ms=ts,
                is_maker=True, reason="end_of_run",
            )
            result.trades.append(
                TradeRecord(
                    timestamp_ms=ts, symbol=sym, action="exit",
                    price=price, size_usd_before=before, size_usd_after=0.0,
                    realized_pnl=self.portfolio.state.realized_pnl_total - realized_before,
                    fee=self.portfolio.state.fees_paid_total - fees_before,
                    score=0.0, leverage=0.0, reason="end_of_run",
                )
            )
        # One final equity point so the curve ends at the closed-out equity.
        result.equity_curve.append(self.portfolio.equity)
        result.timestamps.append(ts)
        result.drawdown_curve.append(self.portfolio.drawdown_pct)
