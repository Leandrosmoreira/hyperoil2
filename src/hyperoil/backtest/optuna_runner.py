"""Optuna runner — walk-forward optimization of strategy parameters.

Splits data into train/test windows, optimizes on train, validates on test.
Penalizes drawdown and tail risk, not just P&L.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import optuna
import pandas as pd

from hyperoil.backtest.metrics import compute_metrics
from hyperoil.backtest.replay_engine import ReplayEngine
from hyperoil.backtest.simulator import SimulationResult, Simulator
from hyperoil.config import AppConfig, GridLevelConfig
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


@dataclass
class WalkForwardFold:
    """A single fold of walk-forward validation."""
    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    best_params: dict[str, Any] = field(default_factory=dict)
    train_pnl: float = 0.0
    test_pnl: float = 0.0
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0


@dataclass
class OptimizationResult:
    """Result of a full walk-forward optimization."""
    folds: list[WalkForwardFold] = field(default_factory=list)
    best_params: dict[str, Any] = field(default_factory=dict)
    aggregate_test_pnl: float = 0.0
    aggregate_test_sharpe: float = 0.0


class OptunaRunner:
    """Walk-forward optimization with Optuna.

    Uses a combined objective that balances P&L with drawdown risk:
        score = net_pnl - drawdown_penalty * max_drawdown
    """

    def __init__(
        self,
        base_config: AppConfig,
        df_left: pd.DataFrame,
        df_right: pd.DataFrame,
        n_folds: int = 3,
        train_ratio: float = 0.7,
        n_trials: int = 50,
        drawdown_penalty: float = 2.0,
    ) -> None:
        self._base_config = base_config
        self._df_left = df_left
        self._df_right = df_right
        self._n_folds = n_folds
        self._train_ratio = train_ratio
        self._n_trials = n_trials
        self._dd_penalty = drawdown_penalty

    def run(self) -> OptimizationResult:
        """Execute walk-forward optimization across all folds."""
        folds = self._create_folds()
        result = OptimizationResult()

        for fold_info in folds:
            fold = self._optimize_fold(fold_info)
            result.folds.append(fold)

            log.info(
                "fold_complete",
                fold=fold.fold_index,
                train_pnl=round(fold.train_pnl, 2),
                test_pnl=round(fold.test_pnl, 2),
                train_sharpe=round(fold.train_sharpe, 2),
                test_sharpe=round(fold.test_sharpe, 2),
            )

        # Aggregate results
        result.aggregate_test_pnl = sum(f.test_pnl for f in result.folds)
        test_sharpes = [f.test_sharpe for f in result.folds if f.test_sharpe != 0]
        result.aggregate_test_sharpe = (
            sum(test_sharpes) / len(test_sharpes) if test_sharpes else 0.0
        )

        # Best params from best performing fold
        if result.folds:
            best_fold = max(result.folds, key=lambda f: f.test_pnl)
            result.best_params = best_fold.best_params

        log.info(
            "optimization_complete",
            folds=len(result.folds),
            total_test_pnl=round(result.aggregate_test_pnl, 2),
            avg_test_sharpe=round(result.aggregate_test_sharpe, 2),
        )

        return result

    def _create_folds(self) -> list[dict]:
        """Create walk-forward fold boundaries."""
        n = len(self._df_left)
        fold_size = n // self._n_folds
        folds = []

        for i in range(self._n_folds):
            fold_start = i * fold_size
            fold_end = (i + 1) * fold_size if i < self._n_folds - 1 else n

            train_size = int((fold_end - fold_start) * self._train_ratio)
            train_start = fold_start
            train_end = fold_start + train_size
            test_start = train_end
            test_end = fold_end

            folds.append({
                "fold_index": i,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
            })

        return folds

    def _optimize_fold(self, fold_info: dict) -> WalkForwardFold:
        """Run Optuna optimization on a single fold."""
        fold = WalkForwardFold(**fold_info)

        train_left = self._df_left.iloc[fold.train_start:fold.train_end].reset_index(drop=True)
        train_right = self._df_right.iloc[fold.train_start:fold.train_end].reset_index(drop=True)
        test_left = self._df_left.iloc[fold.test_start:fold.test_end].reset_index(drop=True)
        test_right = self._df_right.iloc[fold.test_start:fold.test_end].reset_index(drop=True)

        # Optimize on train set
        study = optuna.create_study(direction="maximize")
        study.optimize(
            lambda trial: self._objective(trial, train_left, train_right),
            n_trials=self._n_trials,
            show_progress_bar=False,
        )

        best_params = study.best_params
        fold.best_params = best_params

        # Evaluate on train set
        train_config = self._apply_params(best_params)
        train_replay = ReplayEngine(train_left, train_right)
        train_sim = Simulator(train_config)
        train_result = train_sim.run(train_replay)
        train_metrics = compute_metrics(train_result)
        fold.train_pnl = train_metrics.total_net_pnl
        fold.train_sharpe = train_metrics.sharpe_ratio

        # Validate on test set
        test_replay = ReplayEngine(test_left, test_right)
        test_sim = Simulator(train_config)  # same params as train
        test_result = test_sim.run(test_replay)
        test_metrics = compute_metrics(test_result)
        fold.test_pnl = test_metrics.total_net_pnl
        fold.test_sharpe = test_metrics.sharpe_ratio

        return fold

    def _objective(
        self,
        trial: optuna.Trial,
        df_left: pd.DataFrame,
        df_right: pd.DataFrame,
    ) -> float:
        """Optuna objective function — maximize risk-adjusted P&L."""
        params = {
            "entry_z": trial.suggest_float("entry_z", 1.0, 2.5, step=0.1),
            "exit_z": trial.suggest_float("exit_z", 0.1, 0.8, step=0.05),
            "stop_z": trial.suggest_float("stop_z", 3.0, 6.0, step=0.5),
            "z_window": trial.suggest_int("z_window", 100, 500, step=50),
            "beta_window": trial.suggest_int("beta_window", 50, 300, step=50),
            "base_notional_usd": trial.suggest_float(
                "base_notional_usd", 50.0, 500.0, step=50.0,
            ),
            "cooldown_bars": trial.suggest_int("cooldown_bars", 1, 10),
        }

        config = self._apply_params(params)
        replay = ReplayEngine(df_left, df_right)
        sim = Simulator(config)
        result = sim.run(replay)
        metrics = compute_metrics(result)

        # Combined objective: P&L minus drawdown penalty
        score = metrics.total_net_pnl - self._dd_penalty * metrics.max_drawdown_usd

        # Penalize low trade count (overfitting risk)
        if metrics.total_trades < 5:
            score -= 100.0

        return score

    def _apply_params(self, params: dict[str, Any]) -> AppConfig:
        """Create a new config with trial parameters applied."""
        data = self._base_config.model_dump()

        if "entry_z" in params:
            data["grid"]["entry_z"] = params["entry_z"]
            # Regenerate levels based on entry_z
            base_z = params["entry_z"]
            data["grid"]["levels"] = [
                {"z": base_z, "mult": 1.0},
                {"z": base_z + 0.5, "mult": 1.2},
                {"z": base_z + 1.0, "mult": 1.5},
                {"z": base_z + 1.5, "mult": 2.0},
            ]
        if "exit_z" in params:
            data["grid"]["exit_z"] = params["exit_z"]
        if "stop_z" in params:
            data["grid"]["stop_z"] = params["stop_z"]
        if "z_window" in params:
            data["signal"]["z_window"] = params["z_window"]
        if "beta_window" in params:
            data["signal"]["beta_window"] = params["beta_window"]
        if "base_notional_usd" in params:
            data["sizing"]["base_notional_usd"] = params["base_notional_usd"]
        if "cooldown_bars" in params:
            data["grid"]["cooldown_bars"] = params["cooldown_bars"]

        return AppConfig.model_validate(data)
