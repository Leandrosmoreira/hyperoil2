"""Validation #1: row count = (last_ts - first_ts) / 4h + 1.

OK if delta == 0 for every symbol.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def main() -> int:
    parquet_dir = Path("data/donchian")
    files = sorted(parquet_dir.glob("*.parquet"))
    if not files:
        print(f"No parquet files found in {parquet_dir.resolve()}")
        return 1

    print(f"{'Symbol':<15}{'rows':>8}{'expected':>12}{'delta':>10}")
    print("-" * 45)
    n_bad = 0
    for f in files:
        df = pd.read_parquet(f)
        ts = df["timestamp_ms"].sort_values()
        first, last = ts.iloc[0], ts.iloc[-1]
        expected = (last - first) // (4 * 3600 * 1000) + 1
        delta = len(df) - expected
        flag = "" if delta == 0 else "  <-- MISMATCH"
        if delta != 0:
            n_bad += 1
        print(f"{f.stem:<15}{len(df):>8}{expected:>12}{delta:>+10}{flag}")

    print("-" * 45)
    print(f"Mismatches: {n_bad}/{len(files)}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
