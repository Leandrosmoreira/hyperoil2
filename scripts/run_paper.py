#!/usr/bin/env python3
"""Run HyperOil in paper trading mode.

Usage:
    python scripts/run_paper.py
    python scripts/run_paper.py --config config.yaml --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio

from hyperoil.config import apply_env_overrides, load_config, load_env
from hyperoil.core.orchestrator import Orchestrator
from hyperoil.observability.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HyperOil paper trading")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--log-format", default="console", choices=["json", "console"])
    args = parser.parse_args()

    setup_logging(level=args.log_level, fmt=args.log_format)

    config = load_config(args.config)
    env = load_env()

    # Force paper mode
    data = config.model_dump()
    data["execution"]["mode"] = "paper"
    from hyperoil.config import AppConfig
    config = AppConfig.model_validate(data)

    config = apply_env_overrides(config, env)

    orchestrator = Orchestrator(config, env)
    asyncio.run(orchestrator.start())


if __name__ == "__main__":
    main()
