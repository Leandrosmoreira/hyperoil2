"""Tests for REST client circuit breaker logic."""

from __future__ import annotations

import time

from hyperoil.market_data.rest_client import CircuitBreaker


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        cb = CircuitBreaker(max_failures=3, cooldown_sec=60)
        assert not cb.is_open

    def test_opens_after_max_failures(self) -> None:
        cb = CircuitBreaker(max_failures=3, cooldown_sec=60)
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert cb.is_open

    def test_success_resets_count(self) -> None:
        cb = CircuitBreaker(max_failures=3, cooldown_sec=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open  # only 2 consecutive

    def test_auto_reset_after_cooldown(self) -> None:
        cb = CircuitBreaker(max_failures=2, cooldown_sec=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open

        # Wait for cooldown
        time.sleep(0.15)
        assert not cb.is_open  # should auto-reset

    def test_stays_open_during_cooldown(self) -> None:
        cb = CircuitBreaker(max_failures=2, cooldown_sec=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open
        # Cooldown is 60s, so still open
        assert cb.is_open
