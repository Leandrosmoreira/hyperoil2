"""Validation #4: all timestamps aligned on 4h UTC boundaries.

Every timestamp_ms must satisfy ts % (4*3600*1000) == 0.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

INTERVAL_MS = 4 * 3600 * 1000


def main() -> int:
    parquet_dir = Path("data/donchian")
    n_bad = 0

    for f in sorted(parquet_dir.glob("*.parquet")):
        df = pd.read_parquet(f)
        ts = df["timestamp_ms"]
        misaligned = ts[ts % INTERVAL_MS != 0]
        if len(misaligned):
            print(f"{f.stem:<15} {len(misaligned)} misaligned  first_bad={misaligned.iloc[0]}")
            n_bad += 1
        else:
            print(f"{f.stem:<15} aligned OK")

    print("-" * 40)
    print(f"Symbols with misaligned timestamps: {n_bad}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
