"""Validation #6: no gaps > 4h between consecutive candles."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def main() -> int:
    parquet_dir = Path("data/donchian")
    n_bad = 0

    for f in sorted(parquet_dir.glob("*.parquet")):
        df = pd.read_parquet(f).sort_values("timestamp_ms")
        diffs = df["timestamp_ms"].diff().dropna()
        gaps = diffs[diffs > 4 * 3600 * 1000]
        if len(gaps):
            n_bad += 1
            worst_idx = gaps.idxmax()
            worst_ms = int(df.loc[worst_idx, "timestamp_ms"])
            worst_dt = datetime.fromtimestamp(worst_ms / 1000, tz=timezone.utc)
            print(
                f"{f.stem:<15} gaps={len(gaps):>4}  "
                f"max={gaps.max()/3600000:>6.1f}h  at={worst_dt}"
            )
        else:
            print(f"{f.stem:<15} no gaps")

    print("-" * 55)
    print(f"Symbols with gaps > 4h: {n_bad}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
