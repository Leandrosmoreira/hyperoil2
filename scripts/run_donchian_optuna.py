"""Walk-forward Optuna optimization for the Donchian strategy.

    python scripts/run_donchian_optuna.py --trials 200 --folds 24
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hyperoil.donchian.backtest.optuna_runner import DonchianOptunaRunner
from hyperoil.donchian.config import load_donchian_config


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run walk-forward Optuna study")
    p.add_argument("--config", default="donchian_config.yaml")
    p.add_argument("--trials", type=int, default=200)
    p.add_argument("--folds", type=int, default=24)
    p.add_argument("--min-train-months", type=int, default=9)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="data/donchian/optuna_best.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_donchian_config(args.config)
    runner = DonchianOptunaRunner(
        cfg=cfg,
        parquet_dir=cfg.storage.parquet_dir,
        n_trials=args.trials,
        n_folds=args.folds,
        min_train_months=args.min_train_months,
        seed=args.seed,
    )
    study = runner.run_study()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "best_value": study.best_value,
        "best_params": study.best_params,
        "n_trials": len(study.trials),
        "n_folds": len(runner.folds),
    }, indent=2))
    print(f"Best value: {study.best_value:.4f}")
    print(f"Best params written to {out_path}")


if __name__ == "__main__":
    main()
