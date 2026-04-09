"""Configuration for the Donchian Ensemble strategy (Pydantic v2)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from hyperoil.donchian.types import LOOKBACKS_4H, AssetClass


class AssetConfig(BaseModel):
    """Configuration for a single asset."""
    symbol: str
    hl_ticker: str
    dex_prefix: str = "xyz"
    asset_class: AssetClass
    yfinance_ticker: str | None = None
    binance_symbol: str | None = None
    needs_ffill: bool = False
    invert_price: bool = False  # Apply 1/x to OHLC (e.g. yfinance USDEUR -> EURUSD)


class UniverseConfig(BaseModel):
    """The 25-asset universe."""
    assets: list[AssetConfig]

    @field_validator("assets")
    @classmethod
    def validate_unique_symbols(cls, v: list[AssetConfig]) -> list[AssetConfig]:
        symbols = [a.symbol for a in v]
        if len(symbols) != len(set(symbols)):
            msg = "Duplicate symbols in universe"
            raise ValueError(msg)
        return v


class DonchianSignalConfig(BaseModel):
    """Signal parameters."""
    lookbacks: list[int] = Field(default_factory=lambda: list(LOOKBACKS_4H))
    ema_period: int = 200
    min_score_entry: float = 0.33
    interval: str = "4h"

    @field_validator("lookbacks")
    @classmethod
    def validate_min_lookbacks(cls, v: list[int]) -> list[int]:
        if len(v) < 2:
            msg = "At least 2 lookbacks required"
            raise ValueError(msg)
        return sorted(v)


class RiskParityConfig(BaseModel):
    """Risk parity weighting parameters."""
    vol_window: int = 30          # Days for volatility calculation
    rebal_threshold: float = 0.20  # Rebalance if weight deviates > 20%
    rebal_frequency: str = "weekly"


class DonchianSizingConfig(BaseModel):
    """Position sizing parameters."""
    vol_target_annual: float = 0.25   # 25% a.a. total portfolio
    vol_factor_cap: float = 3.0       # Max vol adjustment factor
    max_position_pct: float = 0.40    # Max 40% of capital per asset
    cash_reserve_pct: float = 0.20    # 20% cash always reserved
    score_thresholds: dict[str, float] = Field(default_factory=lambda: {
        "half_pos": 0.33,    # score >= 0.33 → 0.5x sizing
        "full_pos": 0.55,    # score >= 0.55 → 1.0x sizing
        "lever_1_5x": 0.55,  # leverage 1.5x
        "lever_2x": 0.70,    # leverage 2x
        "lever_3x": 0.85,    # leverage 3x
    })


class DonchianRiskConfig(BaseModel):
    """Risk management parameters."""
    max_drawdown_pct: float = 0.20     # 20% → close everything
    dd_reduce_15_pct: float = 0.15     # 15% → max leverage 1x
    dd_reduce_10_pct: float = 0.10     # 10% → max leverage 1.5x
    min_cash_pct: float = 0.10         # Emergency cash floor
    trailing_stop_never_recedes: bool = True


class DonchianBacktestConfig(BaseModel):
    """Backtest and optimization parameters."""
    start_date: str = "2023-04-01"
    end_date: str = "2026-04-01"
    min_train_months: int = 9
    n_trials: int = 200
    initial_capital: float = 1000.0
    fee_taker_bps: float = 5.0
    fee_maker_bps: float = 1.5
    slippage_bps: float = 1.0


class OrderPolicyConfig(BaseModel):
    """Order entry/exit policy. PROJECT RULE: maker post-only by default."""
    entry_order_type: Literal["limit_maker", "limit_aggressive", "market"] = "limit_maker"
    exit_order_type: Literal["limit_maker", "limit_aggressive", "market"] = "limit_maker"
    emergency_exit_order_type: Literal["limit_aggressive", "market"] = "market"
    post_only_retry_offset_bps: float = 1.0
    post_only_max_retries: int = 3
    unfilled_cancel_after_bars: int = 1


class DonchianStorageConfig(BaseModel):
    """Storage paths for Donchian data."""
    parquet_dir: str = "data/donchian"
    sqlite_path: str = "data/hyperoil.db"   # Shared with pair trading
    jsonl_dir: str = "data/jsonl"


class DonchianObservabilityConfig(BaseModel):
    """Observability for Donchian orchestrator."""
    health_port: int = 9091   # Different from pair trading (9090)
    log_level: str = "INFO"


class DonchianAppConfig(BaseModel):
    """Root configuration for the Donchian Ensemble strategy."""
    universe: UniverseConfig
    signal: DonchianSignalConfig = DonchianSignalConfig()
    risk_parity: RiskParityConfig = RiskParityConfig()
    sizing: DonchianSizingConfig = DonchianSizingConfig()
    risk: DonchianRiskConfig = DonchianRiskConfig()
    backtest: DonchianBacktestConfig = DonchianBacktestConfig()
    storage: DonchianStorageConfig = DonchianStorageConfig()
    observability: DonchianObservabilityConfig = DonchianObservabilityConfig()
    order_policy: OrderPolicyConfig = OrderPolicyConfig()
    execution_mode: Literal["paper", "live"] = "paper"


def load_donchian_config(config_path: str = "donchian_config.yaml") -> DonchianAppConfig:
    """Load and validate Donchian config from YAML."""
    path = Path(config_path)
    if not path.exists():
        msg = f"Donchian config file not found: {config_path}"
        raise FileNotFoundError(msg)

    with open(path) as f:
        raw = yaml.safe_load(f)

    return DonchianAppConfig.model_validate(raw or {})
