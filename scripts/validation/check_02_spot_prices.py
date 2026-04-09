"""Validation #2: spot-check 9 random prices for manual comparison.

Prints 3 random OHLC rows from 3 symbols (BTC, GOLD, NVDA).
YOU must manually compare these against TradingView / Yahoo / CoinGecko
at the same UTC timestamp. This is the only check that hits an external
source of truth — do not skip it.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SYMBOLS = ["hyna_BTC", "xyz_GOLD", "xyz_NVDA"]


def main() -> int:
    parquet_dir = Path("data/donchian")
    random.seed(42)

    for sym in SYMBOLS:
        f = parquet_dir / f"{sym}.parquet"
        if not f.exists():
            print(f"MISSING: {f}")
            continue
        df = pd.read_parquet(f).sort_values("timestamp_ms").reset_index(drop=True)
        idx = sorted(random.sample(range(len(df)), 3))
        print(f"\n=== {sym} ({len(df)} rows) ===")
        print(f"  {'UTC time':<20}  {'Open':>11}  {'High':>11}  {'Low':>11}  {'Close':>11}")
        for i in idx:
            r = df.iloc[i]
            dt = datetime.fromtimestamp(r.timestamp_ms / 1000, tz=timezone.utc)
            print(
                f"  {dt.strftime('%Y-%m-%d %H:%M'):<20}  "
                f"{r.open:>11.4f}  {r.high:>11.4f}  {r.low:>11.4f}  {r.close:>11.4f}"
            )

    print()
    print("=" * 70)
    print("MANUAL STEP:")
    print("  1. Open TradingView -> change chart to 4h timeframe, UTC timezone")
    print("  2. For each printed row, find that candle on the chart")
    print("  3. Compare OHLC — must agree within ~0.1% (crypto) or exactly (tradfi)")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
