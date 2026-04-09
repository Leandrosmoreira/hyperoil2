"""Validation #13: forex direction matches the perp it tracks.

Hyperliquid xyz:EUR perp should track EURUSD (EUR appreciation = up).
Hyperliquid xyz:JPY perp should track... USDJPY? EURJPY? — needs spec.

Yahoo Finance ticker conventions:
  EUR=X     -> USD/EUR  (USD base, EUR quote): UP means USD strong / EUR weak
  EURUSD=X  -> EUR/USD  (EUR base, USD quote): UP means EUR strong / USD weak
  USDJPY=X  -> USD/JPY  (USD base, JPY quote): UP means USD strong / JPY weak
  JPY=X     -> USD/JPY  (alias)

If we use EUR=X (=USDEUR) but the perp tracks EURUSD, ALL signals are inverted.

Reference 2023-04 -> 2026-04 macro reality:
  EURUSD: rose from ~1.08 to ~1.14 (EUR appreciated)
  USDEUR: fell from ~0.926 to ~0.877
  USDJPY: rose from ~133 to ~155 (JPY weakened)
  JPYUSD: fell from ~0.0075 to ~0.0064
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

EXPECTED = {
    # symbol -> (expected_first_close, expected_last_close, perp_direction_label)
    "xyz_EUR": (
        # If using EUR=X (USDEUR), values should be ~0.92 -> ~0.87 (EUR up = number down)
        # If using EURUSD=X, values should be ~1.08 -> ~1.14 (EUR up = number up)
        # The perp on Hyperliquid xyz:EUR most likely is "long EUR vs USD"
        # so we WANT the data to go UP when EUR appreciates.
        ("USDEUR (EUR=X) — INVERTED for EUR perp", 0.85, 0.95),
        ("EURUSD direct — CORRECT for EUR perp",   1.05, 1.20),
    ),
    "xyz_JPY": (
        ("USDJPY (USDJPY=X / JPY=X) — direction depends on perp spec", 130, 165),
        ("JPYUSD inverse",                                              0.0050, 0.0080),
    ),
}


def detect_convention(first: float, last: float, candidates: list) -> str | None:
    for label, lo, hi in candidates:
        if lo <= first <= hi and lo <= last <= hi:
            return label
    return None


def main() -> int:
    parquet_dir = Path("data/donchian")
    issues = 0

    for sym, candidates in EXPECTED.items():
        f = parquet_dir / f"{sym}.parquet"
        if not f.exists():
            print(f"{sym}: MISSING")
            issues += 1
            continue
        df = pd.read_parquet(f).sort_values("timestamp_ms").reset_index(drop=True)
        first = float(df.iloc[0].close)
        last = float(df.iloc[-1].close)
        delta_pct = (last / first - 1) * 100

        match = detect_convention(first, last, candidates)
        print(f"{sym}")
        print(f"  first_close={first:.4f}  last_close={last:.4f}  delta={delta_pct:+.2f}%")
        if match:
            print(f"  detected convention: {match}")
            if "INVERTED" in match:
                print(f"  >>> WARNING: number went DOWN. If perp expects EUR-up=number-up,")
                print(f"      the signal will be INVERTED. MUST flip in collector or strategy.")
                issues += 1
        else:
            print(f"  >>> UNRECOGNIZED price range — MUST manually verify which pair this is.")
            issues += 1
        print()

    print("-" * 60)
    print(f"Forex direction issues: {issues}")
    print()
    print("DECISION REQUIRED: For each forex symbol, the dev must verify:")
    print("  1. What pair does the Hyperliquid xyz:<X> perp actually track?")
    print("  2. Does our data series move in the SAME direction?")
    print("  3. If not, flip in collector (1/x) or in signal engine.")
    return 0 if issues == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
