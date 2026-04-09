#!/usr/bin/env python3
"""Run the Donchian Ensemble strategy in paper trading mode.

Usage:
    python scripts/run_donchian_paper.py
    python scripts/run_donchian_paper.py --config donchian_config.yaml --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio

from hyperoil.config import load_env
from hyperoil.donchian.config import load_donchian_config
from hyperoil.donchian.core.orchestrator import DonchianOrchestrator
from hyperoil.observability.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Donchian Ensemble paper trading")
    parser.add_argument("--config", default="donchian_config.yaml", help="Config file path")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--log-format", default="json", choices=["json", "console"])
    args = parser.parse_args()

    setup_logging(level=args.log_level, fmt=args.log_format)

    cfg = load_donchian_config(args.config)
    env = load_env()

    # Force paper mode regardless of what config says
    cfg = cfg.model_copy(update={"execution_mode": "paper"})

    orchestrator = DonchianOrchestrator(cfg, env)
    asyncio.run(orchestrator.start())


if __name__ == "__main__":
    main()
