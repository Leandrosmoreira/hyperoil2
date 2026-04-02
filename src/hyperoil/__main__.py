"""Entry point for HyperOil v2."""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="hyperoil",
        description="HyperOil v2 — Pair Trading Bot for Hyperliquid DEX",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--mode",
        choices=["live", "paper", "collect", "backtest"],
        default=None,
        help="Override execution mode",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override log level",
    )
    parser.add_argument(
        "--log-format",
        choices=["json", "console"],
        default=None,
        help="Override log format",
    )

    args = parser.parse_args()

    # Load config
    from hyperoil.config import apply_env_overrides, load_config, load_env

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    env = load_env()
    config = apply_env_overrides(config, env)

    # Apply CLI overrides
    if args.mode:
        data = config.model_dump()
        data["execution"]["mode"] = args.mode
        from hyperoil.config import AppConfig
        config = AppConfig.model_validate(data)

    if args.log_level:
        data = config.model_dump()
        data["observability"]["log_level"] = args.log_level
        from hyperoil.config import AppConfig
        config = AppConfig.model_validate(data)

    if args.log_format:
        data = config.model_dump()
        data["observability"]["log_format"] = args.log_format
        from hyperoil.config import AppConfig
        config = AppConfig.model_validate(data)

    # Setup logging
    from hyperoil.observability.logger import setup_logging

    setup_logging(
        level=config.observability.log_level,
        fmt=config.observability.log_format,
    )

    from hyperoil.observability.logger import get_logger

    log = get_logger("main")
    log.info(
        "hyperoil_starting",
        version="2.0.0",
        mode=config.execution.mode,
        pair=f"{config.symbols.left}/{config.symbols.right}",
    )

    # Run orchestrator
    from hyperoil.core.orchestrator import Orchestrator

    orchestrator = Orchestrator(config, env)

    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        log.info("keyboard_interrupt")
    except Exception:
        log.exception("fatal_error")
        sys.exit(1)


if __name__ == "__main__":
    main()
