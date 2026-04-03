#!/usr/bin/env python3
"""Collect historical candle data from Hyperliquid for CL and BRENTOIL.

Downloads 15m candles via REST API with pagination, saves to CSV.

Usage:
    python scripts/collect_data.py
    python scripts/collect_data.py --interval 15m --days 90 --output data/
"""

from __future__ import annotations

import argparse
import asyncio
import time

import pandas as pd

from hyperoil.config import MarketDataConfig, SymbolsConfig
from hyperoil.market_data.rest_client import RestClient


async def collect_symbol(
    client: RestClient,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Collect candles for a single symbol."""
    print(f"  Fetching {symbol} ({interval}) ...")

    candles = await client.fetch_candles_paginated(
        symbol=symbol,
        interval=interval,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
        max_candles=100_000,
    )

    if not candles:
        print(f"  WARNING: No candles returned for {symbol}")
        return pd.DataFrame()

    rows = []
    for c in candles:
        rows.append({
            "timestamp_ms": int(c.get("T", c.get("t", 0))),
            "open": float(c.get("o", 0)),
            "high": float(c.get("h", 0)),
            "low": float(c.get("l", 0)),
            "close": float(c.get("c", 0)),
            "volume": float(c.get("v", 0)),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp_ms").drop_duplicates(subset="timestamp_ms").reset_index(drop=True)

    print(f"  {symbol}: {len(df)} candles collected")
    if not df.empty:
        start_dt = pd.to_datetime(df["timestamp_ms"].iloc[0], unit="ms")
        end_dt = pd.to_datetime(df["timestamp_ms"].iloc[-1], unit="ms")
        print(f"  Range: {start_dt} → {end_dt}")

    return df


async def main_async(args: argparse.Namespace) -> None:
    symbols = SymbolsConfig(left="CL", right="BRENTOIL")
    config = MarketDataConfig()
    client = RestClient(symbols=symbols, config=config)

    await client.start()

    try:
        now_ms = int(time.time() * 1000)
        days_ms = args.days * 24 * 60 * 60 * 1000
        start_ms = now_ms - days_ms

        print(f"Collecting {args.days} days of {args.interval} candles...")
        print(f"Output directory: {args.output}")
        print()

        # Collect both symbols
        df_cl = await collect_symbol(client, "CL", args.interval, start_ms, now_ms)
        await asyncio.sleep(1)  # rate limit courtesy
        df_brent = await collect_symbol(client, "BRENTOIL", args.interval, start_ms, now_ms)

        # Save to CSV
        if not df_cl.empty:
            path_cl = f"{args.output}/cl_{args.interval}.csv"
            df_cl.to_csv(path_cl, index=False)
            print(f"\nSaved: {path_cl} ({len(df_cl)} rows)")

        if not df_brent.empty:
            path_brent = f"{args.output}/brent_{args.interval}.csv"
            df_brent.to_csv(path_brent, index=False)
            print(f"Saved: {path_brent} ({len(df_brent)} rows)")

        # Summary
        if not df_cl.empty and not df_brent.empty:
            # Check alignment
            common = set(df_cl["timestamp_ms"]) & set(df_brent["timestamp_ms"])
            print(f"\nAligned bars: {len(common)} / CL={len(df_cl)} / BRENT={len(df_brent)}")
            print("Data collection complete!")
        else:
            print("\nWARNING: One or both symbols returned no data.")
            print("This may mean the symbols are not yet available on Hyperliquid HIP-3.")
            print("Check: https://app.hyperliquid.xyz for available perps.")

    finally:
        await client.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect HyperOil historical data")
    parser.add_argument("--interval", default="15m", help="Candle interval (default: 15m)")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30, max: ~30)")
    parser.add_argument("--output", default="data", help="Output directory (default: data/)")
    args = parser.parse_args()

    # Warn about Hyperliquid limits
    if args.days > 30:
        print("\n⚠️  WARNING: Hyperliquid only has ~30 days of historical data available.")
        print("   Requesting more than 30 days may fail or return incomplete data.")
        print("   Capping at 30 days.\n")
        args.days = 30

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
