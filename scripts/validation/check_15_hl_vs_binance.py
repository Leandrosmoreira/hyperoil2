"""Validation #15: Hyperliquid vs Binance basis on BTC.

Our backtest will use BTC prices from Binance, but live execution happens
on Hyperliquid. If the basis is wide or volatile, the backtest is optimistic.

Pull a 30-day window of BTC 4h candles from BOTH venues and report:
  - mean basis (HL_close - Binance_close) in bps
  - max abs basis
  - correlation of returns
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, "src")

from hyperoil.donchian.data.collector import (  # noqa: E402
    fetch_binance_4h,
    fetch_hyperliquid_4h,
)


def main() -> int:
    end = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - pd.Timedelta(days=30)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    print(f"Window: {start} -> {end}")

    bn = fetch_binance_4h("BTCUSDT", start_ms, end_ms)
    hl = fetch_hyperliquid_4h("BTC", start_ms, end_ms)

    print(f"Binance bars: {len(bn)}")
    print(f"HL bars:      {len(hl)}")

    if bn.empty or hl.empty:
        print("FAIL — one of the sources returned no data")
        return 1

    bn = bn[["timestamp_ms", "close"]].rename(columns={"close": "bn_close"})
    hl = hl[["timestamp_ms", "close"]].rename(columns={"close": "hl_close"})
    df = bn.merge(hl, on="timestamp_ms", how="inner").sort_values("timestamp_ms")
    print(f"Aligned bars: {len(df)}")

    if df.empty:
        print("FAIL — no overlapping timestamps between Binance and HL")
        return 1

    df["basis_bps"] = (df["hl_close"] - df["bn_close"]) / df["bn_close"] * 10_000
    bn_ret = df["bn_close"].pct_change().dropna()
    hl_ret = df["hl_close"].pct_change().dropna()
    corr = bn_ret.corr(hl_ret)

    print()
    print(f"basis (bps):  mean={df['basis_bps'].mean():+.2f}  std={df['basis_bps'].std():.2f}")
    print(f"              min={df['basis_bps'].min():+.2f}  max={df['basis_bps'].max():+.2f}")
    print(f"              p1={df['basis_bps'].quantile(0.01):+.2f}  p99={df['basis_bps'].quantile(0.99):+.2f}")
    print(f"return correlation (4h): {corr:.4f}")

    # Heuristics for OK / WARN
    abs_max = df["basis_bps"].abs().max()
    fail = False
    if corr < 0.99:
        print("WARN — return correlation < 0.99 (basis is moving)")
        fail = True
    if abs_max > 50:
        print(f"WARN — max abs basis {abs_max:.0f} bps > 50 bps")
        fail = True
    if not fail:
        print("PASS — basis tight, returns nearly perfectly correlated")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
