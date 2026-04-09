"""Run a single Donchian backtest end-to-end and print the report.

    python scripts/run_donchian_backtest.py [--config donchian_config.yaml]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src/` importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hyperoil.donchian.backtest.metrics import (
    compute_donchian_metrics,
    format_donchian_report,
)
from hyperoil.donchian.backtest.multi_replay import MultiAssetReplayEngine
from hyperoil.donchian.backtest.simulator import DonchianSimulator
from hyperoil.donchian.config import load_donchian_config


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a Donchian backtest")
    p.add_argument("--config", default="donchian_config.yaml")
    p.add_argument("--start", type=int, default=None, help="Start timestamp_ms (inclusive)")
    p.add_argument("--end", type=int, default=None, help="End timestamp_ms (inclusive)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_donchian_config(args.config)
    replay = MultiAssetReplayEngine(
        parquet_dir=cfg.storage.parquet_dir,
        assets=cfg.universe.assets,
        start_ms=args.start,
        end_ms=args.end,
    )
    sim = DonchianSimulator(cfg=cfg, replay=replay)
    result = sim.run()
    metrics = compute_donchian_metrics(result)
    print(format_donchian_report(metrics))


if __name__ == "__main__":
    main()
