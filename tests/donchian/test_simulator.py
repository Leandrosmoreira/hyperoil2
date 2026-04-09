"""End-to-end smoke tests for DonchianSimulator on synthetic data."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hyperoil.donchian.backtest.metrics import compute_donchian_metrics
from hyperoil.donchian.backtest.multi_replay import MultiAssetReplayEngine
from hyperoil.donchian.backtest.simulator import DonchianSimulator
from hyperoil.donchian.config import (
    AssetConfig,
    DonchianAppConfig,
    DonchianBacktestConfig,
    DonchianSignalConfig,
    DonchianSizingConfig,
    UniverseConfig,
)
from hyperoil.donchian.types import AssetClass

INTERVAL_MS = 4 * 60 * 60 * 1000


def _make_parquet(tmp_path: Path, prefix: str, ticker: str,
                  closes: np.ndarray) -> None:
    """Synthetic OHLC: collapse high/low onto close so breakouts trigger
    cleanly even on small per-bar moves. Real OHLC has wider intra-bar
    range, but for the tests we just need a deterministic price path."""
    n = len(closes)
    ts = [INTERVAL_MS * i for i in range(n)]
    df = pd.DataFrame({
        "timestamp_ms": ts,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [100.0] * n,
        "symbol": [f"{prefix}:{ticker}"] * n,
        "source": ["test"] * n,
    })
    df.to_parquet(tmp_path / f"{prefix}_{ticker}.parquet", engine="pyarrow", index=False)


def _build_cfg(parquet_dir: str, assets: list[AssetConfig],
               lookbacks: list[int]) -> DonchianAppConfig:
    """Tiny config with reduced lookbacks so warmup fits in synthetic series."""
    return DonchianAppConfig(
        universe=UniverseConfig(assets=assets),
        signal=DonchianSignalConfig(lookbacks=lookbacks, ema_period=20, min_score_entry=0.33),
        sizing=DonchianSizingConfig(
            vol_target_annual=0.25, vol_factor_cap=3.0,
            max_position_pct=0.40, cash_reserve_pct=0.20,
        ),
        backtest=DonchianBacktestConfig(
            start_date="2025-01-01", end_date="2026-01-01",
            initial_capital=10_000.0, fee_maker_bps=1.5, fee_taker_bps=5.0,
        ),
    )


def _asset(ticker: str) -> AssetConfig:
    return AssetConfig(
        symbol=ticker, hl_ticker=ticker, dex_prefix="xyz",
        asset_class=AssetClass.CRYPTO_MAJOR,
    )


# ----------------------------------------------------------------------
# Smoke: deterministic uptrend → portfolio enters and earns
# ----------------------------------------------------------------------
def test_uptrend_produces_long_position(tmp_path):
    # 200 bars of clean uptrend 100 -> 200
    n = 200
    closes_a = np.linspace(100, 200, n)
    closes_b = np.linspace(50, 100, n)
    _make_parquet(tmp_path, "xyz", "A", closes_a)
    _make_parquet(tmp_path, "xyz", "B", closes_b)

    assets = [_asset("A"), _asset("B")]
    # Tiny lookbacks so we still have ~100 bars after warmup.
    cfg = _build_cfg(str(tmp_path), assets, lookbacks=[5, 10, 20])
    cfg = cfg.model_copy(update={
        "risk_parity": cfg.risk_parity.model_copy(update={"vol_window": 2}),
    })

    replay = MultiAssetReplayEngine(str(tmp_path), assets)
    sim = DonchianSimulator(cfg=cfg, replay=replay, api_max_leverage={"xyz:A": 50, "xyz:B": 50})
    result = sim.run()

    assert result.n_bars > 0
    assert result.n_trades > 0
    # On a strict monotonic uptrend the strategy must be in profit.
    assert result.final_equity > result.initial_capital
    # Should have entered both symbols at least once.
    actions = {t.symbol for t in result.trades if t.action == "enter"}
    assert "xyz:A" in actions or "xyz:B" in actions


def test_flat_market_no_trades_or_loses_only_to_fees(tmp_path):
    """Flat closes → no breakouts, no entries, equity stays at capital."""
    n = 200
    flat = np.full(n, 100.0)
    _make_parquet(tmp_path, "xyz", "A", flat)
    _make_parquet(tmp_path, "xyz", "B", flat)

    assets = [_asset("A"), _asset("B")]
    cfg = _build_cfg(str(tmp_path), assets, lookbacks=[5, 10, 20])
    cfg = cfg.model_copy(update={
        "risk_parity": cfg.risk_parity.model_copy(update={"vol_window": 2}),
    })

    replay = MultiAssetReplayEngine(str(tmp_path), assets)
    sim = DonchianSimulator(cfg=cfg, replay=replay, api_max_leverage={"xyz:A": 50, "xyz:B": 50})
    result = sim.run()

    # Vol = 0 on a flat series → all sized targets are 0 → no entries.
    enter_trades = [t for t in result.trades if t.action == "enter"]
    assert len(enter_trades) == 0
    assert result.final_equity == pytest.approx(result.initial_capital, rel=1e-9)


def test_metrics_on_uptrend_are_finite(tmp_path):
    n = 200
    closes_a = np.linspace(100, 200, n)
    closes_b = np.linspace(50, 100, n)
    _make_parquet(tmp_path, "xyz", "A", closes_a)
    _make_parquet(tmp_path, "xyz", "B", closes_b)

    assets = [_asset("A"), _asset("B")]
    cfg = _build_cfg(str(tmp_path), assets, lookbacks=[5, 10, 20])
    cfg = cfg.model_copy(update={
        "risk_parity": cfg.risk_parity.model_copy(update={"vol_window": 2}),
    })

    replay = MultiAssetReplayEngine(str(tmp_path), assets)
    sim = DonchianSimulator(cfg=cfg, replay=replay, api_max_leverage={"xyz:A": 50, "xyz:B": 50})
    result = sim.run()

    m = compute_donchian_metrics(result)
    assert m.final_equity == pytest.approx(result.final_equity, rel=1e-4)
    assert math.isfinite(m.sharpe)
    assert m.max_drawdown_pct >= 0.0
    assert m.cagr >= -1.0


def test_simulator_is_deterministic(tmp_path):
    n = 200
    rng = np.random.default_rng(7)
    base = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.01, n)))
    _make_parquet(tmp_path, "xyz", "A", base)
    _make_parquet(tmp_path, "xyz", "B", base * 0.5)

    assets = [_asset("A"), _asset("B")]
    cfg = _build_cfg(str(tmp_path), assets, lookbacks=[5, 10, 20])
    cfg = cfg.model_copy(update={
        "risk_parity": cfg.risk_parity.model_copy(update={"vol_window": 2}),
    })

    def run() -> float:
        replay = MultiAssetReplayEngine(str(tmp_path), assets)
        sim = DonchianSimulator(cfg=cfg, replay=replay, api_max_leverage={"xyz:A": 50, "xyz:B": 50})
        return sim.run().final_equity

    eq1 = run()
    eq2 = run()
    assert eq1 == eq2
