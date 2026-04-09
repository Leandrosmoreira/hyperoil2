"""Validation #8: forward-fill distribution by asset class.

Expected:
  Crypto:       ffill ~= 0%
  Stocks/Idx:   ffill ~= 70-80% (nights, weekends, holidays)
  FX:           ffill ~= 25-35% (weekends only)
  Commodities:  ffill ~= 25-45% (overnight + weekends)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# Rough grouping — edit here if you add symbols
GROUPS = {
    "crypto":       ["hyna_BTC", "hyna_ETH", "hyna_BNB", "hyna_XRP",
                     "hyna_SOL", "hyna_HYPE", "hyna_DOGE", "hyna_SUI"],
    "stock":        ["xyz_NVDA", "xyz_TSLA", "xyz_AAPL", "xyz_AMZN", "xyz_MSTR"],
    "index":        ["xyz_SP500", "xyz_XYZ100", "xyz_JP225"],
    "forex":        ["xyz_EUR", "xyz_JPY", "xyz_DXY"],
    "commodity":    ["xyz_GOLD", "xyz_SILVER", "xyz_CL", "xyz_BRENTOIL",
                     "xyz_NATGAS", "xyz_COPPER"],
}


def main() -> int:
    parquet_dir = Path("data/donchian")

    print(f"{'Symbol':<15}{'class':<12}{'ffill%':>10}{'vol=0%':>10}  sources")
    print("-" * 85)

    for cls, syms in GROUPS.items():
        for sym in syms:
            f = parquet_dir / f"{sym}.parquet"
            if not f.exists():
                print(f"{sym:<15}{cls:<12}  MISSING")
                continue
            df = pd.read_parquet(f)
            ffill_pct = (df["source"] == "ffill").mean() * 100 if "source" in df.columns else 0.0
            vol0_pct = (df["volume"] == 0).mean() * 100
            sources = ",".join(sorted(df["source"].unique().tolist())) if "source" in df.columns else "-"
            print(f"{sym:<15}{cls:<12}{ffill_pct:>9.1f}%{vol0_pct:>9.1f}%  {sources}")
        print()

    print("-" * 85)
    print("Expected ranges:")
    print("  crypto:     ffill ~=  0%")
    print("  stock:      ffill ~= 70-80%")
    print("  index:      ffill ~= 70-80%")
    print("  forex:      ffill ~= 25-35%")
    print("  commodity:  ffill ~= 25-45%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
