"""Tests for regime filter and classification."""

from __future__ import annotations

from hyperoil.signals.regime_filter import classify_regime_single
from hyperoil.types import Regime


class TestClassifyRegimeSingle:
    def test_good_regime(self) -> None:
        result = classify_regime_single(
            correlation=0.85,
            vol_regime="normal",
            spread_slope=0.005,
        )
        assert result == Regime.GOOD

    def test_bad_low_correlation(self) -> None:
        result = classify_regime_single(
            correlation=0.40,
            vol_regime="normal",
            spread_slope=0.005,
        )
        assert result == Regime.BAD

    def test_bad_extreme_vol(self) -> None:
        result = classify_regime_single(
            correlation=0.85,
            vol_regime="extreme",
            spread_slope=0.005,
        )
        assert result == Regime.BAD

    def test_bad_strong_trend(self) -> None:
        result = classify_regime_single(
            correlation=0.85,
            vol_regime="normal",
            spread_slope=0.05,  # > max_trend_slope * 2
        )
        assert result == Regime.BAD

    def test_caution_medium_correlation(self) -> None:
        result = classify_regime_single(
            correlation=0.60,  # between 0.50 and 0.70
            vol_regime="normal",
            spread_slope=0.005,
        )
        assert result == Regime.CAUTION

    def test_caution_slight_trend(self) -> None:
        result = classify_regime_single(
            correlation=0.85,
            vol_regime="normal",
            spread_slope=0.015,  # > max_trend_slope but < 2x
        )
        assert result == Regime.CAUTION
