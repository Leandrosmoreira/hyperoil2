#!/usr/bin/env python3
"""Persist already-collected Donchian Parquets into the SQLite database.

Use this after `collect_donchian_data.py --no-db` has produced the Parquet
cache in ``data/donchian/``. This avoids re-hitting yFinance (which can be
flaky) and gives us an idempotent way to refresh the live-warmup DB copy.

Usage:
    python scripts/persist_donchian_to_db.py [--config donchian_config.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as a script without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hyperoil.donchian.config import load_donchian_config
from hyperoil.donchian.data.models import DonchianCandleRecord  # noqa: F401 - register table
from hyperoil.donchian.data.storage import (
    parquet_path,
    read_parquet,
    upsert_candles_to_db,
)
from hyperoil.observability.logger import get_logger, setup_logging
from hyperoil.storage.database import close_db, init_db

log = get_logger("donchian.persist")


async def main_async(config_path: str) -> int:
    cfg = load_donchian_config(config_path)
    await init_db(cfg.storage.sqlite_path)

    n_ok = 0
    n_fail = 0
    total_rows = 0

    print()
    print("=" * 70)
    print(f"Persisting Donchian Parquets -> SQLite ({cfg.storage.sqlite_path})")
    print("=" * 70)
    print(f"{'Symbol':<22}{'Rows':>10}  Status")
    print("-" * 70)

    for asset in cfg.universe.assets:
        dex_sym = f"{asset.dex_prefix}:{asset.hl_ticker}"
        path = parquet_path(cfg.storage.parquet_dir, dex_sym)

        if not path.exists():
            print(f"{dex_sym:<22}{'—':>10}  MISSING parquet")
            n_fail += 1
            continue

        df = read_parquet(cfg.storage.parquet_dir, dex_sym)
        if df.empty:
            print(f"{dex_sym:<22}{0:>10}  EMPTY parquet")
            n_fail += 1
            continue

        n = await upsert_candles_to_db(dex_sym, cfg.signal.interval, df)
        total_rows += n
        n_ok += 1
        print(f"{dex_sym:<22}{n:>10}  OK")

    print("-" * 70)
    print(f"Result: {n_ok}/{len(cfg.universe.assets)} OK, {n_fail} failed, {total_rows} rows upserted")
    print("=" * 70)

    await close_db()
    return 0 if n_fail == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="donchian_config.yaml")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    setup_logging(level=args.log_level)
    rc = asyncio.run(main_async(args.config))
    sys.exit(rc)


if __name__ == "__main__":
    main()
