"""Validation #14: collector is deterministic.

Re-fetch one crypto symbol (Binance) and one tradfi symbol (yfinance) twice
in-process and verify the resulting DataFrames are bit-identical.

If they differ, there is hidden mutable state, a clock-dependent path, or
another concurrency bug we haven't found yet.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, "src")

from hyperoil.donchian.data.collector import (  # noqa: E402
    fetch_binance_4h,
    fetch_yfinance,
    sanitize_ohlc,
)


def hash_df(df: pd.DataFrame) -> str:
    cols = ["timestamp_ms", "open", "high", "low", "close"]
    return hashlib.md5(
        pd.util.hash_pandas_object(df[cols], index=False).values.tobytes()
    ).hexdigest()


def main() -> int:
    n_fail = 0

    # 1) Binance — 30-day window of BTCUSDT
    print("=== Binance BTCUSDT (30d) ===")
    end = datetime(2025, 1, 31, tzinfo=timezone.utc)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    a = fetch_binance_4h("BTCUSDT", start_ms, end_ms)
    b = fetch_binance_4h("BTCUSDT", start_ms, end_ms)
    ha = hash_df(a)
    hb = hash_df(b)
    print(f"  rows_a={len(a)}  rows_b={len(b)}")
    print(f"  hash_a={ha}")
    print(f"  hash_b={hb}")
    if ha == hb and len(a) == len(b):
        print("  PASS — deterministic")
    else:
        print("  FAIL — NON-deterministic Binance fetch")
        n_fail += 1

    # 2) yfinance — daily fetch of NVDA
    print()
    print("=== yfinance NVDA (1y) ===")
    yf_start = datetime(2024, 1, 1)
    yf_end = datetime(2025, 1, 1)
    a = sanitize_ohlc(fetch_yfinance("NVDA", yf_start, yf_end, interval="1d"))
    b = sanitize_ohlc(fetch_yfinance("NVDA", yf_start, yf_end, interval="1d"))
    ha = hash_df(a)
    hb = hash_df(b)
    print(f"  rows_a={len(a)}  rows_b={len(b)}")
    print(f"  hash_a={ha}")
    print(f"  hash_b={hb}")
    if ha == hb and len(a) == len(b):
        print("  PASS — deterministic")
    else:
        print("  FAIL — NON-deterministic yfinance fetch")
        n_fail += 1

    print()
    print("-" * 60)
    print(f"Determinism failures: {n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
