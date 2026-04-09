"""Configuration loading and validation with Pydantic v2."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class SymbolsConfig(BaseModel):
    left: str = "CL"
    right: str = "BRENTOIL"
    dex_prefix: str = "xyz"

    @property
    def left_dex(self) -> str:
        return f"{self.dex_prefix}:{self.left}"

    @property
    def right_dex(self) -> str:
        return f"{self.dex_prefix}:{self.right}"


class MarketDataConfig(BaseModel):
    feed_type: Literal["websocket", "rest_poll"] = "websocket"
    interval: str = "15m"
    stale_timeout_sec: float = 30.0
    reconnect_delay_initial_sec: float = 1.0
    reconnect_delay_max_sec: float = 30.0
    ws_ping_interval_sec: float = 20.0
    ws_ping_timeout_sec: float = 10.0
    rest_circuit_breaker_failures: int = 3
    rest_circuit_breaker_cooldown_sec: float = 60.0


class SignalConfig(BaseModel):
    price_source: Literal["mid", "last"] = "mid"
    spread_mode: Literal["log", "linear"] = "log"
    hedge_mode: Literal["fixed", "vol_adjusted", "rolling_ols", "kalman"] = "rolling_ols"
    hedge_ratio_fixed: float = 1.0
    beta_window: int = 168
    z_window: int = 300
    min_std: float = 0.0001
    correlation_window: int = 168
    volatility_window: int = 168


class GridLevelConfig(BaseModel):
    z: float
    mult: float


class GridConfig(BaseModel):
    entry_z: float = 1.5
    exit_z: float = 0.2
    stop_z: float = 4.5
    cooldown_bars: int = 3
    max_levels: int = 4
    anti_repeat_bars: int = 12
    levels: list[GridLevelConfig] = Field(default_factory=list)

    @field_validator("levels")
    @classmethod
    def validate_levels_sorted(cls, v: list[GridLevelConfig]) -> list[GridLevelConfig]:
        for i in range(1, len(v)):
            if v[i].z <= v[i - 1].z:
                msg = "Grid levels must be sorted by ascending z-score"
                raise ValueError(msg)
        return v


class SizingConfig(BaseModel):
    base_notional_usd: float = 100.0
    hedge_mode: Literal["equal", "beta_adjusted"] = "beta_adjusted"
    max_notional_per_cycle: float = 1000.0
    max_total_notional: float = 2000.0


class RiskConfig(BaseModel):
    max_daily_loss_usd: float = 300.0
    max_cycle_loss_usd: float = 120.0
    max_cycle_minutes: int = 180
    max_drawdown_usd: float = 500.0
    max_drawdown_pct: float = 0.10
    max_single_loss_usd: float = 50.0
    max_mae_z: float = 4.0
    min_correlation: float = 0.60
    max_spread_bps: float = 50.0
    max_consecutive_losses: int = 5
    cooldown_after_stop_bars: int = 12
    pause_on_bad_regime: bool = True


class ExecutionConfig(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    order_type: Literal["market", "limit_aggressive"] = "market"
    reconcile_interval_sec: float = 2.0
    fill_timeout_sec: float = 3.0
    emergency_hedge: bool = True
    max_retries: int = 2


class StorageConfig(BaseModel):
    sqlite_path: str = "data/hyperoil.db"
    jsonl_dir: str = "data/jsonl/"
    state_snapshot_interval_sec: float = 30.0


class ObservabilityConfig(BaseModel):
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    health_port: int = 8080
    dashboard_enabled: bool = True
    dashboard_refresh_ms: int = 500


class BacktestConfig(BaseModel):
    fee_maker_bps: float = 1.0
    fee_taker_bps: float = 3.5
    slippage_fixed_bps: float = 1.0
    slippage_proportional_bps: float = 0.5
    fill_assumption: Literal["conservative", "aggressive"] = "aggressive"


class AppConfig(BaseModel):
    """Root configuration model."""
    symbols: SymbolsConfig = SymbolsConfig()
    market_data: MarketDataConfig = MarketDataConfig()
    signal: SignalConfig = SignalConfig()
    grid: GridConfig = GridConfig()
    sizing: SizingConfig = SizingConfig()
    risk: RiskConfig = RiskConfig()
    execution: ExecutionConfig = ExecutionConfig()
    storage: StorageConfig = StorageConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    backtest: BacktestConfig = BacktestConfig()


class EnvConfig(BaseSettings):
    """Environment variables (secrets)."""
    hyperliquid_private_key: str = ""
    hyperliquid_wallet_address: str = ""
    hyperliquid_api_url: str = "https://api.hyperliquid.xyz"
    hyperoil_execution_mode: str | None = None
    hyperoil_log_level: str | None = None
    hyperoil_health_port: int | None = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Load and validate configuration from YAML file."""
    path = Path(config_path)
    if not path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)

    with open(path) as f:
        raw = yaml.safe_load(f)

    return AppConfig.model_validate(raw or {})


def load_env() -> EnvConfig:
    """Load environment variables."""
    return EnvConfig()


def apply_env_overrides(config: AppConfig, env: EnvConfig) -> AppConfig:
    """Apply environment variable overrides to config."""
    data = config.model_dump()

    if env.hyperoil_execution_mode:
        data["execution"]["mode"] = env.hyperoil_execution_mode
    if env.hyperoil_log_level:
        data["observability"]["log_level"] = env.hyperoil_log_level
    if env.hyperoil_health_port is not None:
        data["observability"]["health_port"] = env.hyperoil_health_port

    return AppConfig.model_validate(data)
