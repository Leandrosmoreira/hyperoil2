"""Validation #12: detect continuous-futures roll discontinuities.

GC=F, CL=F, BZ=F, NG=F, HG=F, SI=F are continuous front-month contracts on
yfinance. The roll happens at expiry, creating a 1-3% gap between the
expiring contract and the next one. Donchian sees this as a real move.

Scan commodity symbols for 4h close-to-close jumps > 3% that occur
near typical front-month expiry windows (last week of each month).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

COMMODITIES = ["xyz_GOLD", "xyz_SILVER", "xyz_CL", "xyz_BRENTOIL", "xyz_NATGAS", "xyz_COPPER"]
THRESHOLD = 0.03  # 3% in a single 4h bar is suspicious for commodities


def main() -> int:
    parquet_dir = Path("data/donchian")
    summary: list[tuple[str, int, int]] = []

    for sym in COMMODITIES:
        f = parquet_dir / f"{sym}.parquet"
        if not f.exists():
            print(f"{sym}: MISSING")
            continue
        df = pd.read_parquet(f).sort_values("timestamp_ms").reset_index(drop=True)
        rets = df["close"].pct_change()
        big = rets[rets.abs() > THRESHOLD]

        # How many of those happen in last week of month (typical roll)
        big_dates = pd.to_datetime(df.loc[big.index, "timestamp_ms"], unit="ms", utc=True)
        n_eom = sum(1 for d in big_dates if d.day >= 24)
        n_total = len(big)
        summary.append((sym, n_total, n_eom))

        print(f"{sym:<14} {n_total:>4} bars |ret|>3%   ({n_eom} in last week of month)")
        if n_total <= 10:
            for idx in big.index:
                ts = int(df.loc[idx, "timestamp_ms"])
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                prev = df.loc[idx - 1, "close"]
                curr = df.loc[idx, "close"]
                marker = " <-- end-of-month (likely roll)" if dt.day >= 24 else ""
                print(f"    {dt}  {prev:>9.4f} -> {curr:>9.4f}  ret={(curr/prev-1)*100:+6.2f}%{marker}")

    print("-" * 60)
    total = sum(s[1] for s in summary)
    eom = sum(s[2] for s in summary)
    print(f"Total >3% bars across commodities: {total}  (end-of-month: {eom})")
    print()
    print("If end-of-month bars dominate, these are roll gaps, not real moves.")
    print("Fix: use back-adjusted continuous contracts (e.g. Nasdaq Data Link)")
    print("     OR splice contracts manually with overlap adjustment.")
    # Don't fail — this is informational; a few rolls are expected.
    # We FAIL only if the count is so high it'd dominate signals.
    return 0 if total < 50 else 1


if __name__ == "__main__":
    sys.exit(main())
