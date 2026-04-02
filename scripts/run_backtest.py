#!/usr/bin/env python3
"""Run a backtest with historical data.

Usage:
    python scripts/run_backtest.py --left data/cl.csv --right data/brent.csv
    python scripts/run_backtest.py --left data/cl.csv --right data/brent.csv --config config.yaml
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from hyperoil.backtest.metrics import compute_metrics, format_report
from hyperoil.backtest.replay_engine import ReplayEngine
from hyperoil.backtest.simulator import Simulator
from hyperoil.config import load_config
from hyperoil.observability.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HyperOil backtest")
    parser.add_argument("--left", required=True, help="CSV file for left leg (CL)")
    parser.add_argument("--right", required=True, help="CSV file for right leg (BRENTOIL)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--log-level", default="WARNING", help="Log level")
    args = parser.parse_args()

    setup_logging(level=args.log_level, fmt="console")

    config = load_config(args.config)

    print(f"Loading data: left={args.left}, right={args.right}")
    df_left = pd.read_csv(args.left)
    df_right = pd.read_csv(args.right)

    print(f"Left: {len(df_left)} bars, Right: {len(df_right)} bars")

    replay = ReplayEngine(df_left, df_right)
    print(f"Aligned: {replay.total_bars} bars")

    sim = Simulator(config)
    print("Running simulation...")
    result = sim.run(replay)

    metrics = compute_metrics(result)
    print(format_report(metrics))

    print(f"\nTrades: {len(result.trades)}")
    for t in result.trades[:10]:
        print(f"  {t.direction:15s} L{t.levels_used} z={t.entry_z_avg:+.2f}→{t.exit_z:+.2f} "
              f"pnl=${t.net_pnl:+.2f} ({t.stop_reason})")
    if len(result.trades) > 10:
        print(f"  ... and {len(result.trades) - 10} more")


if __name__ == "__main__":
    main()
