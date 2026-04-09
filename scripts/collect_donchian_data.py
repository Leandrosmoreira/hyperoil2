#!/usr/bin/env python3
"""Sprint 1: Collect 4h candles for the full Donchian universe.

Pipeline per asset:
  1. Crypto -> Binance (4h native), gap-filled by Hyperliquid if short
  2. Tradfi -> yFinance (1h) -> resampled to 4h -> forward-filled across closures
  3. Validate (n_rows, NaN%, max gap)
  4. Persist:
       - Parquet at {storage.parquet_dir}/{safe_symbol}.parquet
       - SQLite (donchian_candles table) for live warmup

Usage:
    python scripts/collect_donchian_data.py [--config donchian_config.yaml] [--no-db]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as a script without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hyperoil.donchian.config import load_donchian_config
from hyperoil.donchian.data.collector import collect_all_assets
from hyperoil.donchian.data.models import DonchianCandleRecord  # noqa: F401 - register table
from hyperoil.donchian.data.storage import (
    ValidationReport,
    upsert_candles_to_db,
    validate_candles,
    write_parquet,
)
from hyperoil.observability.logger import get_logger, setup_logging
from hyperoil.storage.database import close_db, init_db

log = get_logger("donchian.collect")


async def main_async(config_path: str, persist_db: bool) -> int:
    cfg = load_donchian_config(config_path)
    log.info(
        "collect_start",
        assets=len(cfg.universe.assets),
        start=cfg.backtest.start_date,
        end=cfg.backtest.end_date,
        parquet_dir=cfg.storage.parquet_dir,
    )

    if persist_db:
        await init_db(cfg.storage.sqlite_path)

    Path(cfg.storage.parquet_dir).mkdir(parents=True, exist_ok=True)

    results = await collect_all_assets(cfg)

    reports: list[ValidationReport] = []
    n_ok = 0
    n_fail = 0

    for asset in cfg.universe.assets:
        df, result = results.get(asset.symbol, (None, None))
        if df is None or result is None or not result.ok or df.empty:
            err = result.error if result else "unknown"
            log.error("collect_failed", symbol=asset.symbol, error=err)
            n_fail += 1
            continue

        dex_sym = result.dex_symbol

        # Validate
        report = validate_candles(
            symbol=dex_sym,
            df=df,
            interval_hours=4.0,
            min_rows=cfg.signal.lookbacks[-1] + 200,  # max lookback + buffer
            max_nan_pct=0.05,
        )
        reports.append(report)

        if not report.valid:
            log.warning("validation_failed", symbol=dex_sym, errors=report.errors, rows=report.n_rows)
            # Still persist — operator can decide whether to use partial data
        else:
            log.info(
                "validation_ok",
                symbol=dex_sym,
                rows=report.n_rows,
                nan_pct=round(report.nan_pct, 4),
                max_gap_h=round(report.max_gap_hours, 1),
            )

        # Persist Parquet (always — primary backtest source)
        write_parquet(cfg.storage.parquet_dir, dex_sym, df)

        # Persist to SQLite (optional — used for live warmup)
        if persist_db:
            await upsert_candles_to_db(dex_sym, cfg.signal.interval, df)

        n_ok += 1

    if persist_db:
        await close_db()

    # Summary
    print()
    print("=" * 70)
    print(f"Donchian data collection: {n_ok}/{len(cfg.universe.assets)} OK, {n_fail} failed")
    print("=" * 70)
    print(f"{'Symbol':<22}{'Rows':>8}{'NaN%':>8}{'MaxGap(h)':>12}  Valid")
    print("-" * 70)
    for r in reports:
        flag = "OK  " if r.valid else "WARN"
        print(
            f"{r.symbol:<22}{r.n_rows:>8}{r.nan_pct*100:>7.2f}%{r.max_gap_hours:>11.1f}  {flag}"
        )
    print("=" * 70)

    n_invalid = sum(1 for r in reports if not r.valid)
    if n_fail > 0 or n_invalid > 0:
        print(f"WARNING: {n_fail} failed collection, {n_invalid} failed validation")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Donchian historical candles")
    parser.add_argument("--config", default="donchian_config.yaml", help="Config file path")
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip SQLite persistence (Parquet only)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    setup_logging(level=args.log_level, fmt="console")
    rc = asyncio.run(main_async(args.config, persist_db=not args.no_db))
    sys.exit(rc)


if __name__ == "__main__":
    main()
