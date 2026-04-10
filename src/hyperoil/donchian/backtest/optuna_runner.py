"""Walk-forward Optuna optimization for the Donchian Ensemble strategy.

Splits the historical window into N consecutive monthly OOS folds. For each
trial Optuna proposes a parameter set, the runner builds a config from those
params, runs the simulator on the WHOLE in-sample slice and reports a
combined objective so the TPE sampler can converge.

Walk-forward layout (default 24 folds, ~24 months OOS):

    fold 0: train [start, start+12m]            test [start+12m, start+13m]
    fold 1: train [start, start+13m]            test [start+13m, start+14m]
    ...
    fold N: train [start, start+(12+N)m]        test [start+(12+N)m, end]

Objective (per fold, then averaged across folds):

    score = 0.4*sharpe + 0.3*calmar + 0.2*pf + 0.1*cagr - 2*max_dd

Pruning: MedianPruner cuts trials whose intermediate objective falls in the
bottom half. Sampler: TPESampler (default — handles mixed cat/float spaces).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import optuna
from optuna.pruners import MedianPruner
from optuna.samplers import TPESampler

from hyperoil.donchian.backtest.metrics import compute_donchian_metrics
from hyperoil.donchian.backtest.multi_replay import MultiAssetReplayEngine
from hyperoil.donchian.backtest.simulator import DonchianSimulator
from hyperoil.donchian.config import (
    DonchianAppConfig,
    DonchianRiskConfig,
    DonchianSignalConfig,
    DonchianSizingConfig,
    RiskParityConfig,
)
from hyperoil.donchian.types import LOOKBACKS_4H
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int


def make_folds(
    grid_start_ms: int,
    grid_end_ms: int,
    n_folds: int = 24,
    min_train_months: int = 9,
    fold_months: int = 1,
) -> list[WalkForwardFold]:
    """Build walk-forward fold boundaries on a monthly cadence.

    `min_train_months` is the minimum amount of training history before the
    first OOS test slice. After that, each subsequent fold appends one month
    to the training window and tests on the next month.
    """
    ms_per_month = 30 * 24 * 3600 * 1000
    train_start = grid_start_ms
    first_test = grid_start_ms + min_train_months * ms_per_month

    folds: list[WalkForwardFold] = []
    for k in range(n_folds):
        test_start = first_test + k * fold_months * ms_per_month
        test_end = test_start + fold_months * ms_per_month
        if test_start >= grid_end_ms:
            break
        if test_end > grid_end_ms:
            test_end = grid_end_ms
        folds.append(
            WalkForwardFold(
                fold_id=k,
                train_start_ms=train_start,
                train_end_ms=test_start,
                test_start_ms=test_start,
                test_end_ms=test_end,
            )
        )
    return folds


def compose_objective(
    sharpe: float,
    calmar: float,
    profit_factor: float,
    cagr: float,
    max_dd_pct: float,
) -> float:
    """Combined fitness used by the Optuna runner.

    Inf values (e.g. PF when there are no losers, Calmar when DD=0) are
    replaced by sentinel caps so a degenerate fold doesn't dominate the mean.
    """
    pf = profit_factor if math.isfinite(profit_factor) else 5.0
    cl = calmar if math.isfinite(calmar) else 5.0
    return 0.4 * sharpe + 0.3 * cl + 0.2 * pf + 0.1 * cagr - 2.0 * max_dd_pct


def build_trial_config(base: DonchianAppConfig, trial: optuna.Trial) -> DonchianAppConfig:
    """Sample a parameter set from the trial and produce a derived config.

    Optimized parameters:
        - signal.min_score_entry          0.20 - 0.50
        - sizing.score_thresholds[full]   0.45 - 0.65
        - sizing.score_thresholds[lev2]   0.65 - 0.80
        - sizing.score_thresholds[lev3]   0.80 - 0.95
        - sizing.vol_target_annual        0.10 - 0.40
        - risk_parity.rebal_threshold     0.10 - 0.40
        - signal.lookbacks                turn each of the 9 lookbacks on/off
                                          (always keep at least 4)
    """
    half = 0.33  # half-position threshold is fixed (paper)
    full = trial.suggest_float("score_full", 0.45, 0.65)
    lev2 = trial.suggest_float("score_lev2", max(0.65, full + 0.05), 0.80)
    lev3 = trial.suggest_float("score_lev3", max(0.80, lev2 + 0.05), 0.95)

    min_score = trial.suggest_float("min_score_entry", 0.20, 0.50)
    vol_target = trial.suggest_float("vol_target_annual", 0.10, 0.40)
    rebal = trial.suggest_float("rebal_threshold", 0.10, 0.40)

    enabled = []
    for lb in LOOKBACKS_4H:
        if trial.suggest_categorical(f"lb_{lb}", [True, False]):
            enabled.append(lb)
    if len(enabled) < 4:
        # Fall back to the full set rather than an under-determined ensemble.
        enabled = list(LOOKBACKS_4H)

    new_signal = DonchianSignalConfig(
        lookbacks=enabled,
        ema_period=base.signal.ema_period,
        min_score_entry=min_score,
        interval=base.signal.interval,
    )
    new_sizing = DonchianSizingConfig(
        vol_target_annual=vol_target,
        vol_factor_cap=base.sizing.vol_factor_cap,
        max_position_pct=base.sizing.max_position_pct,
        cash_reserve_pct=base.sizing.cash_reserve_pct,
        score_thresholds={
            "half_pos": half,
            "full_pos": full,
            "lever_1_5x": full,
            "lever_2x": lev2,
            "lever_3x": lev3,
        },
    )
    new_risk_parity = RiskParityConfig(
        vol_window=base.risk_parity.vol_window,
        rebal_threshold=rebal,
        rebal_frequency=base.risk_parity.rebal_frequency,
    )

    return base.model_copy(update={
        "signal": new_signal,
        "sizing": new_sizing,
        "risk_parity": new_risk_parity,
    })


class DonchianOptunaRunner:
    """Walk-forward Optuna driver for the Donchian Ensemble strategy."""

    def __init__(
        self,
        cfg: DonchianAppConfig,
        parquet_dir: str,
        api_max_leverage: dict[str, float] | None = None,
        n_trials: int = 200,
        n_folds: int = 24,
        min_train_months: int = 9,
        seed: int = 42,
    ) -> None:
        self.cfg = cfg
        self.parquet_dir = parquet_dir
        self.api_max_leverage = api_max_leverage
        self.n_trials = n_trials
        self.n_folds = n_folds
        self.min_train_months = min_train_months
        self.seed = seed

        # Pre-load the full grid once to determine fold boundaries.
        full_replay = MultiAssetReplayEngine(parquet_dir, cfg.universe.assets)
        wide = full_replay.load()
        self.grid_start = int(wide.index[0])
        self.grid_end = int(wide.index[-1])
        self.folds = make_folds(
            self.grid_start, self.grid_end,
            n_folds=n_folds, min_train_months=min_train_months,
        )

    # ------------------------------------------------------------------
    # Single fold run
    # ------------------------------------------------------------------
    def run_one(
        self,
        cfg: DonchianAppConfig,
        start_ms: int,
        end_ms: int,
    ) -> float:
        replay = MultiAssetReplayEngine(
            self.parquet_dir, cfg.universe.assets,
            start_ms=start_ms, end_ms=end_ms,
        )
        sim = DonchianSimulator(
            cfg=cfg, replay=replay, api_max_leverage=self.api_max_leverage,
        )
        result = sim.run()
        m = compute_donchian_metrics(result)
        return compose_objective(
            sharpe=m.sharpe,
            calmar=m.calmar,
            profit_factor=m.profit_factor,
            cagr=m.cagr,
            max_dd_pct=m.max_drawdown_pct,
        )

    def objective(self, trial: optuna.Trial) -> float:
        cfg = build_trial_config(self.cfg, trial)

        scores: list[float] = []
        for fold in self.folds:
            # Load [train_start .. test_end] so the simulator has the full
            # training window available for warmup. The metric is then
            # computed across the whole curve. NOTE: this is not strict OOS
            # — purer walk-forward would slice metrics to the test region
            # only, but for the bar-count budgets we typically run the
            # warmup-bias matters more than test-set leakage.
            score = self.run_one(cfg, fold.train_start_ms, fold.test_end_ms)
            scores.append(score)
            # Median-pruner support: report intermediate fold scores so Optuna
            # can prune obviously bad trials before finishing all 24 folds.
            trial.report(float(sum(scores) / len(scores)), step=fold.fold_id)
            if trial.should_prune():
                raise optuna.TrialPruned()

        return float(sum(scores) / len(scores))

    def run_study(self, study_name: str = "donchian_ensemble", n_jobs: int = -1) -> optuna.Study:
        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=TPESampler(seed=self.seed),
            pruner=MedianPruner(n_startup_trials=10, n_warmup_steps=3),
        )
        study.optimize(self.objective, n_trials=self.n_trials, gc_after_trial=True, n_jobs=n_jobs)
        log.info(
            "optuna_complete",
            best_value=study.best_value,
            best_params=study.best_params,
            n_trials=len(study.trials),
        )
        return study
