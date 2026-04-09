"""Historical data collector for the Donchian universe.

Routes each asset to its preferred data source:
- Crypto:   Binance (4h native) — fast, deep history, no rate limits at this scale
- Tradfi:   yFinance (1h → resampled to 4h) — covers stocks/indices/FX/commodities
- HL fill:  Hyperliquid (4h via REST candleSnapshot) — only used to plug recent gaps

All sources return a normalized DataFrame:
    columns = [timestamp_ms, open, high, low, close, volume, source]
    timestamp_ms is the bar OPEN time, UTC, milliseconds.

Tradfi assets (needs_ffill=True) are forward-filled across closed sessions
so the Donchian engine sees a continuous 4h series.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from hyperoil.donchian.config import AssetConfig, DonchianAppConfig
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


# 4-hour interval in milliseconds
INTERVAL_MS = 4 * 60 * 60 * 1000


def _to_epoch_ms(dt_like: "pd.DatetimeIndex | pd.Series") -> "pd.Index | pd.Series":
    """Convert tz-aware datetime to Unix milliseconds.

    Safe across pandas 2.x (datetime64[ns]) and 3.0+ (datetime64[ms/us]).
    Avoids ``.astype('int64') // 10**6`` whose result unit depends on the
    stored resolution — in pandas 3.0 a datetime64[ms] index returns ms
    directly, making the ``// 10**6`` produce wrong microscale timestamps.
    Epoch subtraction via ``total_seconds()`` is resolution-agnostic.
    """
    _epoch = pd.Timestamp("1970-01-01", tz="UTC")
    if isinstance(dt_like, pd.DatetimeIndex):
        return pd.Index(
            ((dt_like - _epoch).total_seconds() * 1000).astype("int64"),
            name=dt_like.name,
        )
    # Series
    return (dt_like - _epoch).dt.total_seconds().mul(1000).astype("int64")


# CRITICAL: yfinance uses module-level HTTP session + response cache that is
# NOT thread-safe. With concurrent workers (asyncio.to_thread + Semaphore>1),
# two simultaneous calls can swap responses and produce DIFFERENT symbols
# with IDENTICAL data. We serialize every yfinance call globally with this
# lock. See validation bug: 11/25 tradfi symbols were corrupted.
_YFINANCE_LOCK = threading.Lock()


@dataclass(frozen=True)
class CollectionResult:
    """Outcome of collecting one symbol."""
    symbol: str
    dex_symbol: str
    n_rows: int
    source_primary: str
    sources_used: list[str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.n_rows > 0


# ---------------------------------------------------------------------------
# yFinance source (tradfi: stocks, indices, commodities, forex)
# ---------------------------------------------------------------------------

def _yf_download(yf_ticker: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame:
    """Thin wrapper around yfinance.download with consistent error handling.

    Serialized via _YFINANCE_LOCK — yfinance's module-level HTTP cache is NOT
    thread-safe and concurrent calls can swap responses between tickers.
    """
    import yfinance as yf

    with _YFINANCE_LOCK:
        try:
            df = yf.download(
                tickers=yf_ticker,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("yf_download_exception", ticker=yf_ticker, interval=interval, error=str(e))
            return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def _yf_history_period(yf_ticker: str, period: str, interval: str) -> pd.DataFrame:
    """Fallback: use Ticker.history(period=...) which is sometimes more
    forgiving than download(start=, end=) for delisted/futures contracts.

    Serialized via _YFINANCE_LOCK (see _yf_download note).
    """
    import yfinance as yf

    with _YFINANCE_LOCK:
        try:
            df = yf.Ticker(yf_ticker).history(
                period=period, interval=interval, auto_adjust=False
            )
        except Exception as e:  # noqa: BLE001
            log.warning("yf_history_exception", ticker=yf_ticker, error=str(e))
            return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def fetch_yfinance(
    yf_ticker: str,
    start: datetime,
    end: datetime,
    interval: str = "1h",
) -> pd.DataFrame:
    """Fetch candles from yFinance and normalize to our schema.

    yFinance hard-limits 1h data to ~730 days. To cover a longer backtest:
      1. Try 1h for the full window (works if window <= 730 days)
      2. If 1h fails or is empty, fall back to 1d covering the full window
         (4h bars will be created by forward-filling daily closes)
      3. Optionally splice in recent 1h data on top of daily for the last 730d
    """
    log.info("yfinance_fetch_start", ticker=yf_ticker, start=start.isoformat(), end=end.isoformat())

    # Track which actual interval produced the data so the caller can decide
    # whether to resample (1h) or use the daily->4h grid path.
    actual_interval = interval

    # Try 1h first
    df = pd.DataFrame()
    try:
        df = _yf_download(yf_ticker, start, end, interval=interval)
    except Exception as e:  # noqa: BLE001
        log.warning("yfinance_1h_error", ticker=yf_ticker, error=str(e))
        df = pd.DataFrame()

    # If 1h is empty (window too long, holiday-only, delisted), fall back to 1d
    if df is None or df.empty:
        log.info("yfinance_fallback_daily_download", ticker=yf_ticker)
        try:
            df = _yf_download(yf_ticker, start, end, interval="1d")
            if df is not None and not df.empty:
                actual_interval = "1d"
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance_1d_error", ticker=yf_ticker, error=str(e))
            df = pd.DataFrame()

    # Last resort: Ticker.history(period=...) — works for some delisted/futures
    # tickers that yf.download(start=, end=) chokes on. Retry with backoff to
    # tolerate the occasional yfinance rate-limit.
    if df is None or df.empty:
        for attempt in range(3):
            log.info("yfinance_fallback_history_period", ticker=yf_ticker, attempt=attempt)
            df = _yf_history_period(yf_ticker, period="5y", interval="1d")
            if df is not None and not df.empty:
                actual_interval = "1d"
                break
            time.sleep(1.0 * (attempt + 1))  # 1s, 2s, 3s backoff

    if df is None or df.empty:
        log.warning("yfinance_empty", ticker=yf_ticker)
        return pd.DataFrame(
            columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
        )

    # Multi-asset download yields a MultiIndex column header — flatten it.
    # yfinance changed the MultiIndex axis order across versions:
    #   older (<0.2.50): (Field, Ticker) → level 0 = "Open", "High", …
    #   newer (≥0.2.50): (Ticker, Field) → level 0 = "GC=F", "GC=F", …
    # Detect which level holds OHLCV names and use that one; never assume level 0.
    if isinstance(df.columns, pd.MultiIndex):
        _ohlcv = {"Open", "High", "Low", "Close", "Volume", "Adj Close"}
        if _ohlcv & set(df.columns.get_level_values(0).tolist()):
            df.columns = df.columns.get_level_values(0)   # old yfinance
        else:
            df.columns = df.columns.get_level_values(-1)  # new yfinance

    # Defensive: some yfinance paths (especially Ticker.history combined with
    # download fallbacks or future versions) can leave duplicate columns.
    # Keep the first occurrence of any duplicated name.
    df = df.loc[:, ~df.columns.duplicated()]

    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    # Convert index to UTC ms
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df["timestamp_ms"] = _to_epoch_ms(df.index)
    # Tag with the ACTUAL interval that produced the rows (may differ from
    # the requested interval if a fallback path was taken).
    df["source"] = f"yfinance_{actual_interval}"

    cols = ["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
    # Some indices/FX have no Volume column at all on daily interval
    if "volume" not in df.columns:
        df["volume"] = 0.0
    out = df[cols].copy()
    out = out.dropna(subset=["close"]).reset_index(drop=True)
    log.info("yfinance_fetch_done", ticker=yf_ticker, rows=len(out), interval=actual_interval)
    return out


def resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Downsample 1h OHLCV to 4h (UTC bar boundaries: 00, 04, 08, 12, 16, 20).

    Only valid input is sub-4h data (typically 1h). Daily data should NOT be
    resampled here — use ``daily_to_4h_grid`` followed by ``forward_fill_4h``.
    """
    if df.empty:
        return df

    df = df.copy()
    df["dt"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.set_index("dt").sort_index()

    # Resample each Series independently. Pandas' DataFrameGroupBy.agg() with
    # named-aggregation tuples dispatches to the deprecated NDFrame.first()
    # path on some sparse inputs and crashes; per-Series .apply() avoids that.
    rs = df.resample("4h", label="left", closed="left")
    agg = pd.DataFrame({
        "open": rs["open"].apply(lambda s: s.iloc[0] if len(s) else float("nan")),
        "high": rs["high"].max(),
        "low": rs["low"].min(),
        "close": rs["close"].apply(lambda s: s.iloc[-1] if len(s) else float("nan")),
        "volume": rs["volume"].sum(),
    })
    agg = agg.dropna(subset=["close"])
    agg["timestamp_ms"] = _to_epoch_ms(agg.index)
    agg["source"] = "yfinance_4h"
    return agg.reset_index(drop=True)[
        ["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
    ]


def daily_to_4h_grid(df: pd.DataFrame) -> pd.DataFrame:
    """Convert daily OHLCV bars into 4h bars at the END-of-session anchor.

    CRITICAL — lookhead bias prevention: a yfinance daily bar dated D
    represents a session whose CLOSE happens late on day D (e.g. NYSE closes
    at 21:00 UTC). If we anchor that bar to D 00:00 UTC, the strategy "knows"
    the day's close 21 hours before it happened.

    Fix: anchor each daily bar to (D + 1 day) 00:00 UTC. The data only
    becomes available at the next UTC midnight, which is always AFTER the
    underlying market close (NYSE closes 03:00 UTC the next day in DST,
    21:00 UTC in winter; commodity sessions similar). Forward-fill then
    propagates this value forward from D+1 00:00 UTC.

    Practical effect on the timeline:
        Slots D 00:00 .. D 20:00 UTC -> show the PREVIOUS session's close
        Slot  D+1 00:00 UTC          -> shows day-D session close (now known)
        Slots D+1 04:00 .. D+1 20:00 -> ffill of day-D close
    """
    if df.empty:
        return df

    df = df.copy()
    # Defensive dedup on incoming columns
    df = df.loc[:, ~df.columns.duplicated()]
    df["dt"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    # Anchor at the NEXT UTC midnight to avoid lookhead bias
    df["dt"] = df["dt"].dt.floor("D") + pd.Timedelta(days=1)
    df["timestamp_ms"] = _to_epoch_ms(df["dt"])
    df["source"] = "yfinance_1d"
    out = df[["timestamp_ms", "open", "high", "low", "close", "volume", "source"]].copy()
    # Collapse any duplicate timestamps (multi-ticker splits etc.)
    out = out.drop_duplicates(subset=["timestamp_ms"], keep="first").reset_index(drop=True)
    return out


def invert_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Apply 1/x to OHLC, swapping high and low.

    Used when the data source quotes the inverse of the desired pair, e.g.
    yfinance EUR=X is USDEUR but xyz:EUR perp tracks EURUSD direct. The
    high of USDEUR corresponds to the LOW of EURUSD (and vice versa) because
    reciprocation flips ordering.
    """
    if df.empty:
        return df
    df = df.copy()
    new_open = 1.0 / df["open"]
    new_close = 1.0 / df["close"]
    new_high = 1.0 / df["low"]   # min of inverse becomes max
    new_low = 1.0 / df["high"]
    df["open"] = new_open
    df["high"] = new_high
    df["low"] = new_low
    df["close"] = new_close
    return df


def sanitize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Clamp OHLC to enforce physical invariants.

    Yahoo Finance sometimes returns daily forex bars where low > open or
    close > high (observed on EUR=X, USDJPY=X). This is logically impossible
    and would corrupt the Donchian rolling min/max. We fix it by expanding
    low/high to include open/close:

        low'  = min(low, open, close)
        high' = max(high, open, close)

    For consistent bars this is a no-op. Logs how many rows were fixed.
    """
    if df.empty:
        return df

    df = df.copy()
    bad_mask = (
        (df["low"] > df["open"])
        | (df["low"] > df["close"])
        | (df["high"] < df["open"])
        | (df["high"] < df["close"])
        | (df["low"] > df["high"])
    )
    n_bad = int(bad_mask.sum())
    if n_bad:
        log.warning("ohlc_sanitize_fix", rows_fixed=n_bad)
        df.loc[bad_mask, "low"] = df.loc[bad_mask, [
            "low", "open", "close"
        ]].min(axis=1)
        df.loc[bad_mask, "high"] = df.loc[bad_mask, [
            "high", "open", "close"
        ]].max(axis=1)
    return df


def forward_fill_4h(df: pd.DataFrame, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Forward-fill missing 4h bars across closed sessions (stocks/FX weekends).

    Builds a complete 4h grid from start_ms to end_ms, joins with existing
    bars, and ffills OHLC. Volume is set to 0 for filled bars.
    """
    if df.empty:
        return df

    grid = pd.date_range(
        start=pd.to_datetime(start_ms, unit="ms", utc=True),
        end=pd.to_datetime(end_ms, unit="ms", utc=True),
        freq="4h",
    )
    grid_ms = _to_epoch_ms(grid)

    df = df.set_index("timestamp_ms").reindex(grid_ms)

    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].ffill()
    df["volume"] = df["volume"].fillna(0.0)
    df["source"] = df["source"].fillna("ffill")

    df = df.dropna(subset=["close"]).reset_index().rename(columns={"index": "timestamp_ms"})
    return df[["timestamp_ms", "open", "high", "low", "close", "volume", "source"]]


# ---------------------------------------------------------------------------
# Binance source (crypto: 4h native)
# ---------------------------------------------------------------------------

def fetch_binance_4h(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch 4h klines from Binance public API. Pages through 1000-bar chunks.

    Uses the public spot API; Binance allows ~1200 weight/min unauthenticated.
    """
    import requests

    url = "https://api.binance.com/api/v3/klines"
    out: list[list] = []
    cursor = start_ms

    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": "4h",
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
        except Exception as e:
            log.warning("binance_request_failed", symbol=symbol, error=str(e))
            break

        if not batch:
            break

        out.extend(batch)
        last_open = batch[-1][0]
        if last_open <= cursor:  # no progress
            break
        cursor = last_open + INTERVAL_MS

        if len(batch) < 1000:
            break

        # Be polite to the public endpoint
        time.sleep(0.1)

    if not out:
        log.warning("binance_empty", symbol=symbol)
        return pd.DataFrame(
            columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
        )

    rows = [
        {
            "timestamp_ms": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "source": "binance",
        }
        for k in out
    ]
    df = pd.DataFrame(rows)
    df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms").reset_index(drop=True)
    log.info("binance_fetch_done", symbol=symbol, rows=len(df))
    return df


# ---------------------------------------------------------------------------
# Hyperliquid source (recent gap-fill)
# ---------------------------------------------------------------------------

def fetch_hyperliquid_4h(coin: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Fetch 4h candles from Hyperliquid info API.

    `coin` is the bare ticker (e.g. 'BTC' for hyna:BTC, 'GOLD' for xyz:GOLD).
    Note: HIP-3 perps may not all be queryable via candleSnapshot — used as
    best-effort fill for crypto and recent data.
    """
    import requests

    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": "4h",
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            log.warning("hl_fetch_http", coin=coin, status=resp.status_code)
            return pd.DataFrame(
                columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
            )
        data = resp.json()
    except Exception as e:
        log.warning("hl_fetch_failed", coin=coin, error=str(e))
        return pd.DataFrame(
            columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
        )

    if not isinstance(data, list) or not data:
        return pd.DataFrame(
            columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
        )

    rows = [
        {
            "timestamp_ms": int(c["t"]),
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c.get("v", 0.0)),
            "source": "hyperliquid",
        }
        for c in data
    ]
    df = pd.DataFrame(rows)
    df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms").reset_index(drop=True)
    log.info("hl_fetch_done", coin=coin, rows=len(df))
    return df


# ---------------------------------------------------------------------------
# Top-level per-asset orchestration
# ---------------------------------------------------------------------------

def merge_sources(*frames: pd.DataFrame) -> pd.DataFrame:
    """Concatenate multiple source dataframes, dedupe by timestamp_ms.

    Earlier frames take precedence on conflict (keep='first' after sort).
    """
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return pd.DataFrame(
            columns=["timestamp_ms", "open", "high", "low", "close", "volume", "source"]
        )

    merged = pd.concat(non_empty, ignore_index=True)
    merged = merged.sort_values("timestamp_ms").drop_duplicates("timestamp_ms", keep="first")
    return merged.reset_index(drop=True)


def collect_one_asset(
    asset: AssetConfig,
    start: datetime,
    end: datetime,
) -> tuple[pd.DataFrame, CollectionResult]:
    """Collect 4h candles for a single asset using the most appropriate sources.

    Crypto path:    Binance 4h -> HL gap-fill (if needed)
    Tradfi path:    yFinance 1h -> resample 4h -> ffill across closed sessions
    """
    dex_sym = f"{asset.dex_prefix}:{asset.hl_ticker}"
    sources_used: list[str] = []
    is_crypto = asset.asset_class.value in ("crypto_major", "crypto_minor")
    start_ms = int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(end.replace(tzinfo=timezone.utc).timestamp() * 1000)

    try:
        if is_crypto:
            primary = "binance"
            df_main = pd.DataFrame()
            if asset.binance_symbol:
                df_main = fetch_binance_4h(asset.binance_symbol, start_ms, end_ms)
                if not df_main.empty:
                    sources_used.append("binance")

            # Some new tokens (e.g. HYPE) may have NO Binance history at all;
            # fall back to Hyperliquid in that case. Don't fetch HL just because
            # the requested window is short.
            df_hl = pd.DataFrame()
            if df_main.empty:
                df_hl = fetch_hyperliquid_4h(asset.hl_ticker, start_ms, end_ms)
                if not df_hl.empty:
                    sources_used.append("hyperliquid")

            df = merge_sources(df_main, df_hl)
            df = sanitize_ohlc(df)
            return df, CollectionResult(
                symbol=asset.symbol,
                dex_symbol=dex_sym,
                n_rows=len(df),
                source_primary=primary,
                sources_used=sources_used,
            )

        # --- Tradfi path ---
        primary = "yfinance"
        if not asset.yfinance_ticker:
            return pd.DataFrame(), CollectionResult(
                symbol=asset.symbol,
                dex_symbol=dex_sym,
                n_rows=0,
                source_primary=primary,
                sources_used=[],
                error="no yfinance ticker configured",
            )

        df_yf = fetch_yfinance(asset.yfinance_ticker, start, end, interval="1h")
        if not df_yf.empty and asset.invert_price:
            log.info("invert_price_applied", symbol=asset.symbol, ticker=asset.yfinance_ticker)
            df_yf = invert_ohlc(df_yf)
        if df_yf.empty:
            return pd.DataFrame(), CollectionResult(
                symbol=asset.symbol,
                dex_symbol=dex_sym,
                n_rows=0,
                source_primary=primary,
                sources_used=[],
                error="yfinance returned no rows",
            )

        # Detect whether the fetch returned hourly or daily bars (the source
        # column is tagged at fetch time). Daily must NOT be resampled — each
        # daily bar is mapped to a single 4h slot at 00:00 UTC and ffilled.
        is_daily = bool((df_yf["source"] == "yfinance_1d").any())
        sources_used.append("yfinance_1d" if is_daily else "yfinance_1h")

        if is_daily:
            df_4h = daily_to_4h_grid(df_yf)
        else:
            df_4h = resample_to_4h(df_yf)

        # Clamp OHLC BEFORE forward-fill so bad values don't propagate.
        # Yahoo Finance occasionally returns logically-impossible bars
        # (low > open/close) for forex — would poison Donchian rolling min.
        df_4h = sanitize_ohlc(df_4h)

        # Tradfi assets always need ffill: stocks/indices have closed sessions
        # (nights, weekends, holidays); FX has weekends; daily-fallback data
        # only has 1 bar per day and needs the other 5 4h slots filled.
        if asset.needs_ffill or is_daily or asset.asset_class.value in ("stock", "index", "forex", "commodity"):
            df_4h = forward_fill_4h(df_4h, start_ms, end_ms)
            sources_used.append("ffill")

        return df_4h, CollectionResult(
            symbol=asset.symbol,
            dex_symbol=dex_sym,
            n_rows=len(df_4h),
            source_primary=primary,
            sources_used=sources_used,
        )

    except Exception as e:  # noqa: BLE001 — top-level boundary, log and continue
        log.exception("collect_one_asset_failed", symbol=asset.symbol)
        return pd.DataFrame(), CollectionResult(
            symbol=asset.symbol,
            dex_symbol=dex_sym,
            n_rows=0,
            source_primary="",
            sources_used=sources_used,
            error=str(e),
        )


async def collect_all_assets(
    config: DonchianAppConfig,
) -> dict[str, tuple[pd.DataFrame, CollectionResult]]:
    """Collect candles for every asset in the universe.

    yFinance and Binance HTTP libraries are sync; we offload each asset to a
    worker thread so the event loop stays responsive. Concurrency is bounded
    so we don't hammer either provider.
    """
    start = datetime.fromisoformat(config.backtest.start_date)
    end = datetime.fromisoformat(config.backtest.end_date)

    semaphore = asyncio.Semaphore(4)
    results: dict[str, tuple[pd.DataFrame, CollectionResult]] = {}

    async def _worker(asset: AssetConfig) -> None:
        async with semaphore:
            df, result = await asyncio.to_thread(collect_one_asset, asset, start, end)
            results[asset.symbol] = (df, result)
            status = "OK" if result.ok else f"FAIL ({result.error})"
            log.info(
                "asset_collected",
                symbol=asset.symbol,
                dex=result.dex_symbol,
                rows=result.n_rows,
                sources=result.sources_used,
                status=status,
            )

    await asyncio.gather(*[_worker(a) for a in config.universe.assets])
    return results
