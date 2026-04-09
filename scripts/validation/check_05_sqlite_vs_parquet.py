"""Validation #5: SQLite must exactly match Parquet (row count + content hash).

Compares donchian_candles table vs each parquet file.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pandas as pd

DB_PATH = "data/hyperoil.db"
PARQUET_DIR = Path("data/donchian")


def hash_df(df: pd.DataFrame) -> str:
    return hashlib.md5(
        pd.util.hash_pandas_object(df, index=False).values.tobytes()
    ).hexdigest()[:12]


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    n_bad = 0

    print(f"{'Symbol':<15}{'pq_rows':>9}{'db_rows':>9}  {'pq_hash':<14}{'db_hash':<14}{'match':>7}")
    print("-" * 75)

    for f in sorted(PARQUET_DIR.glob("*.parquet")):
        pq = pd.read_parquet(f).sort_values("timestamp_ms").reset_index(drop=True)
        sym = pq["symbol"].iloc[0] if "symbol" in pq.columns else f.stem.replace("_", ":", 1)

        cols = ["timestamp_ms", "open", "high", "low", "close"]
        db = pd.read_sql(
            "SELECT timestamp_ms, open, high, low, close FROM donchian_candles "
            "WHERE symbol = ? ORDER BY timestamp_ms",
            conn,
            params=(sym,),
        )

        # Cast to same dtypes for hash comparison
        pq_cmp = pq[cols].astype({"timestamp_ms": "int64"}).reset_index(drop=True)
        db_cmp = db.astype({"timestamp_ms": "int64"}).reset_index(drop=True)

        h_pq = hash_df(pq_cmp)
        h_db = hash_df(db_cmp)
        rows_match = len(pq) == len(db)
        hash_match = h_pq == h_db
        ok = rows_match and hash_match
        flag = "OK" if ok else "FAIL"
        if not ok:
            n_bad += 1

        print(f"{sym:<15}{len(pq):>9}{len(db):>9}  {h_pq:<14}{h_db:<14}{flag:>7}")

    conn.close()
    print("-" * 75)
    print(f"Mismatches: {n_bad}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
