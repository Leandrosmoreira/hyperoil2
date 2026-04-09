"""Validation #16: timezone anchoring for daily-fallback data — lookhead bias check.

A daily NVDA bar dated "2024-02-01" represents the NYSE session that closed
at 21:00 UTC on Feb 1. yfinance returns Date=2024-02-01.

In daily_to_4h_grid we floor the date to UTC midnight, putting the bar at
2024-02-01 00:00 UTC — which is 21 HOURS BEFORE the close actually happened.

If forward-fill then propagates that close to bars 04:00, 08:00, 12:00 UTC
of Feb 1, the strategy would "know" the day's close in the morning UTC time.
That is a 21h lookhead bias.

This test verifies whether the close at 2024-02-01 00:00 UTC in our parquet
equals the daily close from yfinance (which would prove the bug).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROBES = [
    # (symbol, parquet_file, yfinance_ticker, test_date_str)
    ("NVDA",  "xyz_NVDA.parquet",  "NVDA", "2024-02-01"),
    ("AAPL",  "xyz_AAPL.parquet",  "AAPL", "2024-02-01"),
    ("GOLD",  "xyz_GOLD.parquet",  "GC=F", "2024-04-12"),
]


def main() -> int:
    import yfinance as yf

    parquet_dir = Path("data/donchian")
    n_lookhead = 0

    for sym, fname, yf_ticker, date_str in PROBES:
        f = parquet_dir / fname
        if not f.exists():
            print(f"{sym}: MISSING parquet")
            continue

        df = pd.read_parquet(f).sort_values("timestamp_ms").reset_index(drop=True)
        df["dt"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)

        # Day's bars from our parquet
        day = df[df["dt"].dt.strftime("%Y-%m-%d") == date_str].copy()
        if day.empty:
            print(f"{sym}: no bars on {date_str}")
            continue

        # Reference daily bar from yfinance
        ref = yf.download(
            yf_ticker,
            start=date_str,
            end=(pd.Timestamp(date_str) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if isinstance(ref.columns, pd.MultiIndex):
            ref.columns = ref.columns.get_level_values(0)
        if ref.empty:
            print(f"{sym}: yfinance ref empty")
            continue
        ref_close = float(ref["Close"].iloc[0])
        ref_open  = float(ref["Open"].iloc[0])

        first_bar = day.iloc[0]
        first_close = float(first_bar["close"])
        first_hour = first_bar["dt"].strftime("%H:%M UTC")

        print(f"=== {sym} {date_str} ===")
        print(f"  yfinance daily:  open={ref_open:.4f}  close={ref_close:.4f}")
        print(f"  parquet first bar of day  ({first_hour}): close={first_close:.4f}")

        # If first_close at 00:00 UTC == daily close (which happens at ~21:00 UTC)
        # then we have lookhead bias.
        if abs(first_close - ref_close) < 1e-6:
            print(f"  >>> LOOKHEAD: 00:00 UTC bar already contains the 21:00 UTC close")
            n_lookhead += 1
        elif abs(first_close - ref_open) < 1e-6:
            print(f"  OK: 00:00 UTC bar uses the OPEN — no lookhead, but stale")
        else:
            print(f"  ?: doesn't match either open or close — investigate")
        print()

    print("-" * 60)
    print(f"Lookhead-bias bars detected: {n_lookhead}/{len(PROBES)}")
    if n_lookhead > 0:
        print()
        print("FIX REQUIRED: anchor daily bars at the END of session, not the start.")
        print("For US stocks: shift to next day's 00:00 UTC (= ~3h after NYSE close)")
        print("For futures:  similar — shift one bar forward")
    return 1 if n_lookhead > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
