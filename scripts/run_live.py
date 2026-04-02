#!/usr/bin/env python3
"""Run HyperOil in LIVE trading mode.

WARNING: This will execute real orders with real money on Hyperliquid.
Ensure your .env file has valid credentials.

Usage:
    python scripts/run_live.py
    python scripts/run_live.py --config config.yaml
"""

from __future__ import annotations

import argparse
import asyncio

from hyperoil.config import apply_env_overrides, load_config, load_env
from hyperoil.core.orchestrator import Orchestrator
from hyperoil.observability.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HyperOil LIVE trading")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--log-format", default="json", choices=["json", "console"])
    parser.add_argument(
        "--confirm", action="store_true",
        help="Confirm live mode (required for safety)",
    )
    args = parser.parse_args()

    if not args.confirm:
        print("=" * 60)
        print("  WARNING: LIVE TRADING MODE")
        print("  This will execute REAL orders with REAL money.")
        print("  Add --confirm to proceed.")
        print("=" * 60)
        return

    setup_logging(level=args.log_level, fmt=args.log_format)

    config = load_config(args.config)
    env = load_env()

    if not env.hyperliquid_private_key:
        print("ERROR: HYPERLIQUID_PRIVATE_KEY not set in .env")
        return

    # Force live mode
    data = config.model_dump()
    data["execution"]["mode"] = "live"
    from hyperoil.config import AppConfig
    config = AppConfig.model_validate(data)

    config = apply_env_overrides(config, env)

    orchestrator = Orchestrator(config, env)
    asyncio.run(orchestrator.start())


if __name__ == "__main__":
    main()
