"""Tests for configuration loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hyperoil.config import AppConfig, GridConfig, GridLevelConfig


def test_default_config_valid() -> None:
    config = AppConfig()
    assert config.symbols.left == "CL"
    assert config.symbols.right == "BRENTOIL"
    assert config.execution.mode == "paper"
    assert config.grid.max_levels == 4


def test_grid_levels_must_be_sorted() -> None:
    with pytest.raises(ValidationError):
        GridConfig(
            levels=[
                GridLevelConfig(z=2.0, mult=1.0),
                GridLevelConfig(z=1.5, mult=1.2),  # not sorted
            ]
        )


def test_grid_levels_sorted_passes() -> None:
    config = GridConfig(
        levels=[
            GridLevelConfig(z=1.5, mult=1.0),
            GridLevelConfig(z=2.0, mult=1.2),
            GridLevelConfig(z=2.5, mult=1.5),
        ]
    )
    assert len(config.levels) == 3


def test_symbols_dex_format() -> None:
    config = AppConfig()
    assert config.symbols.left_dex == "xyz:CL"
    assert config.symbols.right_dex == "xyz:BRENTOIL"


def test_risk_config_defaults() -> None:
    config = AppConfig()
    assert config.risk.max_daily_loss_usd == 300.0
    assert config.risk.min_correlation == 0.60
    assert config.risk.pause_on_bad_regime is True
