"""Run every automatic validation check in sequence and summarize.

Usage:
    python scripts/validation/run_all.py

Does NOT run check_02 (manual spot-check) — run that separately and
compare the printed candles against TradingView yourself.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CHECKS = [
    ("01_row_count",          "check_01_row_count.py"),
    ("03_ohlc_logic",         "check_03_ohlc_logic.py"),
    ("04_ts_alignment",       "check_04_ts_alignment.py"),
    ("05_sqlite_vs_parquet",  "check_05_sqlite_vs_parquet.py"),
    ("06_gaps",               "check_06_gaps.py"),
    ("07_extreme_returns",    "check_07_extreme_returns.py"),
    ("08_ffill_distribution", "check_08_ffill_distribution.py"),
    ("10_post_only_rule",     "check_10_post_only_rule.py"),
    ("11_stock_splits",       "check_11_stock_splits.py"),
    ("12_futures_rolls",      "check_12_futures_rolls.py"),
    ("13_forex_direction",    "check_13_forex_direction.py"),
    ("14_determinism",        "check_14_determinism.py"),
    ("15_hl_vs_binance",      "check_15_hl_vs_binance.py"),
    ("16_timezone_lookhead",  "check_16_timezone_lookhead.py"),
]


def main() -> int:
    here = Path(__file__).parent
    results: list[tuple[str, int]] = []

    for name, script in CHECKS:
        print()
        print("=" * 78)
        print(f"### {name}")
        print("=" * 78)
        p = subprocess.run([sys.executable, str(here / script)])
        results.append((name, p.returncode))

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"{'Check':<30}  Result")
    print("-" * 45)
    n_pass = 0
    for name, rc in results:
        mark = "PASS" if rc == 0 else "FAIL"
        if rc == 0:
            n_pass += 1
        print(f"{name:<30}  {mark}")
    print("-" * 45)
    print(f"{n_pass}/{len(results)} automatic checks passed")
    print()
    print("Also run MANUALLY:")
    print("  python scripts/validation/check_02_spot_prices.py")
    print("  python scripts/verify_tickers.py   (HIP-3 ticker existence)")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
