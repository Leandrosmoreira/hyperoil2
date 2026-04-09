"""Validation #3: OHLC physical invariants.

For every row: low <= open,close <= high  AND  high >= low.
OK if total invalid == 0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def main() -> int:
    parquet_dir = Path("data/donchian")
    total_bad = 0

    for f in sorted(parquet_dir.glob("*.parquet")):
        df = pd.read_parquet(f)
        bad = df[
            (df["low"] > df["high"])
            | (df["open"] > df["high"]) | (df["open"] < df["low"])
            | (df["close"] > df["high"]) | (df["close"] < df["low"])
        ]
        if len(bad):
            print(f"{f.stem}: {len(bad)} invalid OHLC rows")
            print(bad.head(3).to_string())
            total_bad += len(bad)

    print("-" * 40)
    print(f"Total invalid OHLC rows: {total_bad}")
    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
