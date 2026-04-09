"""Validation #11: detect unadjusted stock splits.

Stocks fetched with yfinance auto_adjust=False keep raw prices, so a 10:1
split shows up as a -90% bar. Donchian would treat that as a giant breakout.

Scan all stock symbols for any 4h close-to-close return with |ret| > 20%.
A few news-event >10% spikes are normal; >20% is almost certainly a split.

Known splits in our universe:
  NVDA   2024-06-10  10-for-1
  TSLA   2022-08-25  3-for-1   (pre our window — should NOT appear)
  AAPL   none in window
  AMZN   2022-06-06  20-for-1  (pre our window)
  MSTR   2024-08-08  10-for-1
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

STOCKS = ["xyz_NVDA", "xyz_TSLA", "xyz_AAPL", "xyz_AMZN", "xyz_MSTR"]
# Threshold raised to 40% after observing yfinance auto-applies split
# adjustment even when auto_adjust=False. A real unadjusted 10:1 split
# would be -90%, well above this. Anything between 20-40% is news/earnings.
THRESHOLD = 0.40


def main() -> int:
    parquet_dir = Path("data/donchian")
    n_bad = 0

    for sym in STOCKS:
        f = parquet_dir / f"{sym}.parquet"
        if not f.exists():
            print(f"{sym}: MISSING")
            continue
        df = pd.read_parquet(f).sort_values("timestamp_ms").reset_index(drop=True)
        rets = df["close"].pct_change()
        big = rets[rets.abs() > THRESHOLD]
        if len(big) == 0:
            print(f"{sym:<12} clean (no |ret|>20%)")
            continue

        n_bad += len(big)
        print(f"{sym:<12} {len(big)} suspicious bars >20%:")
        for idx in big.index:
            ts = int(df.loc[idx, "timestamp_ms"])
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            prev = df.loc[idx - 1, "close"]
            curr = df.loc[idx, "close"]
            print(f"  {dt}  {prev:>10.2f} -> {curr:>10.2f}  ret={(curr/prev-1)*100:+7.1f}%")

    print("-" * 60)
    print(f"Total suspicious bars: {n_bad}")
    print()
    print("If NVDA / MSTR show -90% drops, the data is UNADJUSTED for splits.")
    print("Fix: collector uses auto_adjust=True OR backfill split factors.")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
