"""Tests for position sizing and hedge calculation."""

from __future__ import annotations

from hyperoil.config import GridConfig, GridLevelConfig, SizingConfig
from hyperoil.strategy.position_plan import PositionPlanner


def _default_grid() -> GridConfig:
    return GridConfig(
        levels=[
            GridLevelConfig(z=1.5, mult=1.0),
            GridLevelConfig(z=2.0, mult=1.2),
            GridLevelConfig(z=2.5, mult=1.5),
            GridLevelConfig(z=3.0, mult=2.0),
        ],
    )


def _default_sizing() -> SizingConfig:
    return SizingConfig(
        base_notional_usd=100.0,
        hedge_mode="beta_adjusted",
        max_notional_per_cycle=1000.0,
        max_total_notional=2000.0,
    )


class TestPositionPlanner:
    def test_level_1_sizes(self) -> None:
        planner = PositionPlanner(_default_sizing(), _default_grid())
        sizes = planner.compute_sizes(
            level=1, beta=0.95, price_left=68.50, price_right=72.30,
        )
        assert sizes is not None
        assert sizes.size_left > 0
        assert sizes.size_right > 0
        # Left leg should be base_notional / price
        expected_left = 100.0 / 68.50
        assert abs(sizes.size_left - round(expected_left, 6)) < 0.001

    def test_level_2_has_multiplier(self) -> None:
        planner = PositionPlanner(_default_sizing(), _default_grid())
        s1 = planner.compute_sizes(level=1, beta=0.95, price_left=68.50, price_right=72.30)
        s2 = planner.compute_sizes(level=2, beta=0.95, price_left=68.50, price_right=72.30)
        assert s1 is not None and s2 is not None
        # Level 2 has mult=1.2, so should be 20% larger
        assert s2.size_left > s1.size_left
        assert abs(s2.size_left / s1.size_left - 1.2) < 0.01

    def test_beta_adjusted_sizing(self) -> None:
        planner = PositionPlanner(_default_sizing(), _default_grid())
        sizes = planner.compute_sizes(
            level=1, beta=0.95, price_left=68.50, price_right=72.30,
        )
        assert sizes is not None
        # Right leg should be (base * beta) / price_right
        expected_right = (100.0 * 0.95) / 72.30
        assert abs(sizes.size_right - round(expected_right, 6)) < 0.001

    def test_equal_sizing(self) -> None:
        sizing = SizingConfig(
            base_notional_usd=100.0,
            hedge_mode="equal",
            max_notional_per_cycle=1000.0,
            max_total_notional=2000.0,
        )
        planner = PositionPlanner(sizing, _default_grid())
        sizes = planner.compute_sizes(
            level=1, beta=0.95, price_left=68.50, price_right=72.30,
        )
        assert sizes is not None
        expected_left = 100.0 / 68.50
        expected_right = 100.0 / 72.30
        assert abs(sizes.size_left - round(expected_left, 6)) < 0.001
        assert abs(sizes.size_right - round(expected_right, 6)) < 0.001

    def test_rejects_cycle_limit(self) -> None:
        sizing = SizingConfig(
            base_notional_usd=100.0,
            max_notional_per_cycle=150.0,  # very tight
        )
        planner = PositionPlanner(sizing, _default_grid())
        # First level might fit (~195 notional), but let's check with existing
        sizes = planner.compute_sizes(
            level=1, beta=0.95, price_left=68.50, price_right=72.30,
            existing_notional=100.0,
        )
        assert sizes is None  # should be rejected

    def test_rejects_zero_price(self) -> None:
        planner = PositionPlanner(_default_sizing(), _default_grid())
        sizes = planner.compute_sizes(level=1, beta=0.95, price_left=0, price_right=72.30)
        assert sizes is None

    def test_rejects_negative_beta(self) -> None:
        planner = PositionPlanner(_default_sizing(), _default_grid())
        sizes = planner.compute_sizes(level=1, beta=-0.5, price_left=68.50, price_right=72.30)
        assert sizes is None

    def test_exit_sizes_full(self) -> None:
        planner = PositionPlanner(_default_sizing(), _default_grid())
        exit_sizes = planner.compute_exit_sizes(1.46, 1.38, fraction=1.0)
        assert exit_sizes.size_left == 1.46
        assert exit_sizes.size_right == 1.38

    def test_exit_sizes_partial(self) -> None:
        planner = PositionPlanner(_default_sizing(), _default_grid())
        exit_sizes = planner.compute_exit_sizes(1.46, 1.38, fraction=0.5)
        assert abs(exit_sizes.size_left - 0.73) < 0.01
        assert abs(exit_sizes.size_right - 0.69) < 0.01
