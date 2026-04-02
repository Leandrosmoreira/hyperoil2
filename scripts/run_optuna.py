#!/usr/bin/env python3
"""Run walk-forward optimization with Optuna.

Usage:
    python scripts/run_optuna.py --left data/cl.csv --right data/brent.csv
    python scripts/run_optuna.py --left data/cl.csv --right data/brent.csv --trials 100 --folds 5
"""

from __future__ import annotations

import argparse
import sys

import optuna
import pandas as pd

from hyperoil.backtest.optuna_runner import OptunaRunner
from hyperoil.config import load_config
from hyperoil.observability.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HyperOil Optuna optimization")
    parser.add_argument("--left", required=True, help="CSV file for left leg (CL)")
    parser.add_argument("--right", required=True, help="CSV file for right leg (BRENTOIL)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--trials", type=int, default=50, help="Trials per fold")
    parser.add_argument("--folds", type=int, default=3, help="Walk-forward folds")
    parser.add_argument("--dd-penalty", type=float, default=2.0, help="Drawdown penalty multiplier")
    parser.add_argument("--log-level", default="WARNING", help="Log level")
    args = parser.parse_args()

    setup_logging(level=args.log_level, fmt="console")
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    config = load_config(args.config)

    print(f"Loading data: left={args.left}, right={args.right}")
    df_left = pd.read_csv(args.left)
    df_right = pd.read_csv(args.right)

    print(f"Running optimization: {args.folds} folds x {args.trials} trials")

    runner = OptunaRunner(
        base_config=config,
        df_left=df_left,
        df_right=df_right,
        n_folds=args.folds,
        n_trials=args.trials,
        drawdown_penalty=args.dd_penalty,
    )
    result = runner.run()

    print("\n" + "=" * 60)
    print("  OPTIMIZATION RESULTS")
    print("=" * 60)

    for fold in result.folds:
        print(f"\n  Fold {fold.fold_index}:")
        print(f"    Train P&L: ${fold.train_pnl:+.2f}  Sharpe: {fold.train_sharpe:.2f}")
        print(f"    Test  P&L: ${fold.test_pnl:+.2f}  Sharpe: {fold.test_sharpe:.2f}")
        print(f"    Params: {fold.best_params}")

    print(f"\n  Aggregate Test P&L:    ${result.aggregate_test_pnl:+.2f}")
    print(f"  Aggregate Test Sharpe: {result.aggregate_test_sharpe:.2f}")
    print(f"\n  Best Parameters:")
    for k, v in result.best_params.items():
        print(f"    {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
