"""Validation #7: sanity check on 4h returns — no absurd spikes.

Flags any bar with |close.pct_change()| > 30%. A few such bars per crypto
are OK (big news events). Tradfi should essentially never have >10%.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def main() -> int:
    parquet_dir = Path("data/donchian")

    print(f"{'Symbol':<15}{'max |ret|':>12}{'>30% bars':>12}{'>10% bars':>12}")
    print("-" * 55)

    for f in sorted(parquet_dir.glob("*.parquet")):
        df = pd.read_parquet(f).sort_values("timestamp_ms")
        rets = df["close"].pct_change().dropna()
        max_ret = rets.abs().max() * 100
        n_30 = int((rets.abs() > 0.30).sum())
        n_10 = int((rets.abs() > 0.10).sum())
        print(f"{f.stem:<15}{max_ret:>11.2f}%{n_30:>12}{n_10:>12}")

        # Print the worst offender if > 50%
        if max_ret > 50:
            worst = rets.abs().idxmax()
            dt = datetime.fromtimestamp(df.loc[worst, "timestamp_ms"] / 1000, tz=timezone.utc)
            prev_close = df.loc[worst - 1, "close"] if worst > 0 else None
            curr_close = df.loc[worst, "close"]
            print(f"  >>> worst bar at {dt}: prev={prev_close} -> curr={curr_close}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
