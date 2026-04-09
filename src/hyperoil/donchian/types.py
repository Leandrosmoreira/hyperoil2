"""Shared types, enums, and dataclasses for the Donchian Ensemble strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AssetClass(str, Enum):
    CRYPTO_MAJOR = "crypto_major"    # BTC, ETH, BNB, XRP
    CRYPTO_MINOR = "crypto_minor"    # SOL, HYPE, DOGE, AVAX
    COMMODITY = "commodity"
    STOCK = "stock"
    INDEX = "index"
    FOREX = "forex"


class DonchianAction(str, Enum):
    ENTER = "enter"
    EXIT = "exit"
    INCREASE = "increase"
    DECREASE = "decrease"
    HOLD = "hold"


class MarketRegime(str, Enum):
    TRENDING = "trending"
    CHOPPY = "choppy"
    CRISIS = "crisis"


# Lookback windows in 4H candles (paper: 5-360 days)
LOOKBACKS_4H: list[int] = [30, 60, 120, 180, 360, 540, 900, 1500, 2160]

# Maximum lookback determines warmup requirement
MAX_LOOKBACK = max(LOOKBACKS_4H)  # 2160

# Asset class leverage caps
CLASS_MAX_LEVERAGE: dict[AssetClass, float] = {
    AssetClass.CRYPTO_MAJOR: 2.0,
    AssetClass.CRYPTO_MINOR: 1.5,
    AssetClass.COMMODITY: 3.0,
    AssetClass.STOCK: 2.0,
    AssetClass.INDEX: 3.0,
    AssetClass.FOREX: 3.0,
}


@dataclass(frozen=True)
class AssetInfo:
    """Static info for one asset in the universe."""
    symbol: str             # Internal name: "BTC", "GOLD", etc.
    hl_ticker: str          # Hyperliquid ticker: "BTC", "GOLD", etc.
    dex_prefix: str         # "xyz" or "kntq"
    asset_class: AssetClass
    yfinance_ticker: str | None  # e.g. "BTC-USD", "GC=F", None if no yf source
    binance_symbol: str | None   # e.g. "BTCUSDT", None if no Binance source
    sz_decimals: int = 0
    max_leverage: int = 50
    needs_ffill: bool = False    # Stocks/indices/commodities need forward fill

    @property
    def dex_symbol(self) -> str:
        return f"{self.dex_prefix}:{self.hl_ticker}"


@dataclass(frozen=True)
class DonchianChannel:
    """Upper/lower/mid for one lookback on one asset."""
    lookback: int
    upper: float
    lower: float
    mid: float


@dataclass(frozen=True)
class DonchianSignal:
    """Complete signal output for one asset at one timestamp."""
    symbol: str
    timestamp_ms: int
    score: float              # 0.0 to 1.0 (mean of 9 binary signals)
    dominant_lookback: int    # Lookback that contributed most
    stop_line: float          # Mid of dominant channel
    ema_200: float
    entry_valid: bool         # close > EMA(200) AND score >= 0.33
    channels: list[DonchianChannel] = field(default_factory=list)


@dataclass
class DonchianPosition:
    """State of a single position within the portfolio."""
    symbol: str
    side: str                 # "long" or "short"
    entry_price: float
    current_price: float
    size_usd: float           # Notional in USD
    leverage: float
    trailing_stop: float
    score_at_entry: float
    entry_timestamp_ms: int
    unrealized_pnl: float = 0.0
    bars_held: int = 0


@dataclass
class PortfolioSnapshot:
    """Point-in-time portfolio state."""
    timestamp_ms: int
    equity: float
    cash: float
    peak_equity: float
    drawdown_pct: float
    n_positions: int
    total_exposure_usd: float
    positions: dict[str, DonchianPosition] = field(default_factory=dict)
