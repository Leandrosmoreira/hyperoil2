#!/usr/bin/env python3
"""Sprint 0: Verify all 25 Donchian universe tickers on Hyperliquid API.

Calls the Hyperliquid info endpoint to fetch metadata for all perps,
validates that each ticker exists, reads szDecimals and maxLeverage,
and writes config/ticker_mapping.json.

Usage:
    python scripts/verify_tickers.py [--config donchian_config.yaml]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

# Hyperliquid API endpoints
HL_INFO_URL = "https://api.hyperliquid.xyz/info"


def fetch_dex_meta(dex: str) -> dict:
    """Fetch perp metadata for a specific HIP-3 dex (e.g. 'xyz', 'flx', 'hyna').

    HIP-3 perps are queried via metaAndAssetCtxs with the `dex` parameter.
    Returns the meta dict containing 'universe' list of perp specs.
    """
    resp = requests.post(
        HL_INFO_URL,
        json={"type": "metaAndAssetCtxs", "dex": dex},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and len(data) >= 1:
        return data[0]
    return {}


def fetch_main_meta() -> dict:
    """Fetch main perp metadata (no dex parameter — first/default dex)."""
    resp = requests.post(HL_INFO_URL, json={"type": "meta"}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Donchian tickers on Hyperliquid")
    parser.add_argument("--config", default="donchian_config.yaml", help="Config file path")
    parser.add_argument("--output", default="config/ticker_mapping.json", help="Output JSON path")
    args = parser.parse_args()

    # Load config to get universe
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    import yaml
    with open(config_path) as f:
        raw_config = yaml.safe_load(f)

    assets = raw_config["universe"]["assets"]
    print(f"Verifying {len(assets)} tickers on Hyperliquid HIP-3 dexes...\n")

    # Collect all unique dex prefixes from config
    dex_prefixes = sorted({a.get("dex_prefix", "xyz") for a in assets})
    print(f"Dex prefixes used: {dex_prefixes}\n")

    # Fetch metadata for each dex
    dex_lookups: dict[str, dict[str, dict]] = {}
    for dex in dex_prefixes:
        print(f"Fetching dex={dex} perps...")
        try:
            meta = fetch_dex_meta(dex)
            universe = meta.get("universe", [])
            lookup: dict[str, dict] = {}
            for asset_meta in universe:
                full_name = asset_meta["name"]  # e.g. "xyz:GOLD"
                # Strip the "<dex>:" prefix to get the bare ticker
                bare = full_name.split(":", 1)[1] if ":" in full_name else full_name
                lookup[bare] = {
                    "szDecimals": asset_meta.get("szDecimals", 0),
                    "maxLeverage": asset_meta.get("maxLeverage", 50),
                    "marginTableId": asset_meta.get("marginTableId", 0),
                    "full_name": full_name,
                }
            dex_lookups[dex] = lookup
            print(f"  -> {len(universe)} perps")
        except Exception as e:
            print(f"  ERROR for dex={dex}: {e}")
            dex_lookups[dex] = {}

    # Verify each asset
    found = 0
    not_found = []
    ticker_mapping: dict[str, dict] = {}

    print()
    for asset in assets:
        symbol = asset["symbol"]
        hl_ticker = asset["hl_ticker"]
        prefix = asset.get("dex_prefix", "xyz")

        info = dex_lookups.get(prefix, {}).get(hl_ticker)
        source = f"dex:{prefix}"

        if info:
            sz_dec = info["szDecimals"]
            max_lev = info["maxLeverage"]
            ticker_mapping[symbol] = {
                "hl_ticker": hl_ticker,
                "dex_prefix": prefix,
                "dex_symbol": f"{prefix}:{hl_ticker}",
                "sz_decimals": sz_dec,
                "max_leverage": max_lev,
                "asset_class": asset["asset_class"],
                "source": source,
                "verified": True,
            }
            found += 1
            status = "OK"
            print(f"  {status:>5}  {symbol:>8}  {prefix}:{hl_ticker:<12}  "
                  f"szDec={sz_dec}  maxLev={max_lev}x")
        else:
            not_found.append(symbol)
            ticker_mapping[symbol] = {
                "hl_ticker": hl_ticker,
                "dex_prefix": prefix,
                "dex_symbol": f"{prefix}:{hl_ticker}",
                "sz_decimals": 0,
                "max_leverage": 0,
                "asset_class": asset["asset_class"],
                "source": "not_found",
                "verified": False,
            }
            status = "MISS"
            print(f"  {status:>5}  {symbol:>8}  {prefix}:{hl_ticker:<12}  NOT FOUND")

    # Summary
    print(f"\n{'='*60}")
    print(f"Results: {found}/{len(assets)} tickers found")
    if not_found:
        print(f"Missing: {', '.join(not_found)}")

    # Save ticker_mapping.json
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "total_assets": len(assets),
        "verified": found,
        "missing": not_found,
        "ticker_mapping": ticker_mapping,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nSaved to: {output_path}")

    # Also dump all available perps per dex for debugging
    for dex, lookup in dex_lookups.items():
        print(f"\n--- Available perps in dex={dex} ({len(lookup)}) ---")
        for name in sorted(lookup.keys()):
            info = lookup[name]
            print(f"  {info['full_name']:<22} szDec={info['szDecimals']}  maxLev={info['maxLeverage']}x")

    if not_found:
        print(f"\nWARNING: {len(not_found)} tickers not found. "
              "Check hl_ticker names in donchian_config.yaml.")
        sys.exit(1)
    else:
        print(f"\nAll {found} tickers verified successfully!")


if __name__ == "__main__":
    main()
