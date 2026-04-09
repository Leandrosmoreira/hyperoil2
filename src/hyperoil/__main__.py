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
        "--strategy",
        choices=["pair_trading", "donchian"],
        default="pair_trading",
        help="Which strategy to run (default: pair_trading)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml for pair_trading, "
             "donchian_config.yaml for donchian)",
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

    if args.strategy == "donchian":
        _run_donchian(args)
        return

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


def _run_donchian(args: argparse.Namespace) -> None:
    """Bootstrap and run the Donchian Ensemble orchestrator."""
    from hyperoil.config import load_env
    from hyperoil.donchian.config import load_donchian_config
    from hyperoil.donchian.core.orchestrator import DonchianOrchestrator
    from hyperoil.observability.logger import get_logger, setup_logging

    # Default to donchian_config.yaml if user left --config at the default.
    config_path = args.config if args.config != "config.yaml" else "donchian_config.yaml"

    try:
        cfg = load_donchian_config(config_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.mode in ("paper", "live"):
        cfg = cfg.model_copy(update={"execution_mode": args.mode})

    setup_logging(
        level=args.log_level or cfg.observability.log_level,
        fmt=args.log_format or "json",
    )
    log = get_logger("main_donchian")
    log.info(
        "donchian_starting",
        version="2.0.0",
        mode=cfg.execution_mode,
        n_assets=len(cfg.universe.assets),
        config_path=config_path,
    )

    env = load_env()
    orchestrator = DonchianOrchestrator(cfg=cfg, env=env)

    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        log.info("keyboard_interrupt")
    except Exception:
        log.exception("fatal_error")
        sys.exit(1)


if __name__ == "__main__":
    main()
