"""Async REST client for Hyperliquid API with circuit breaker."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from hyperoil.config import MarketDataConfig, SymbolsConfig
from hyperoil.observability.logger import get_logger

log = get_logger(__name__)


class CircuitBreaker:
    """Simple circuit breaker: opens after N consecutive failures, auto-resets after cooldown."""

    def __init__(self, max_failures: int, cooldown_sec: float) -> None:
        self._max_failures = max_failures
        self._cooldown_sec = cooldown_sec
        self._failure_count = 0
        self._opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._failure_count < self._max_failures:
            return False
        # Check if cooldown has passed
        if time.time() - self._opened_at >= self._cooldown_sec:
            self._failure_count = 0
            log.info("circuit_breaker_reset")
            return False
        return True

    def record_success(self) -> None:
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self._max_failures:
            self._opened_at = time.time()
            log.warning(
                "circuit_breaker_opened",
                failures=self._failure_count,
                cooldown_sec=self._cooldown_sec,
            )


class RestClient:
    """Async REST client for Hyperliquid info API."""

    BASE_URL = "https://api.hyperliquid.xyz"

    def __init__(
        self,
        symbols: SymbolsConfig,
        config: MarketDataConfig,
    ) -> None:
        self._symbols = symbols
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._circuit_breaker = CircuitBreaker(
            max_failures=config.rest_circuit_breaker_failures,
            cooldown_sec=config.rest_circuit_breaker_cooldown_sec,
        )

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    def _coin_name(self, symbol: str) -> str:
        if ":" in symbol:
            return symbol
        return f"{self._symbols.dex_prefix}:{symbol}"

    async def _post(self, payload: dict[str, Any]) -> Any:
        """POST to /info with circuit breaker protection."""
        if self._circuit_breaker.is_open:
            msg = "Circuit breaker is open, request blocked"
            raise ConnectionError(msg)

        if not self._session:
            msg = "REST client not started"
            raise RuntimeError(msg)

        try:
            async with self._session.post(
                f"{self.BASE_URL}/info",
                json=payload,
            ) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", "5"))
                    log.warning("rest_rate_limited", retry_after=retry_after)
                    self._circuit_breaker.record_failure()
                    await asyncio.sleep(retry_after)
                    msg = "Rate limited"
                    raise ConnectionError(msg)

                if resp.status != 200:
                    body = await resp.text()
                    self._circuit_breaker.record_failure()
                    log.warning("rest_error", status=resp.status, body=body[:500])
                    msg = f"HTTP {resp.status}: {body[:200]}"
                    raise ConnectionError(msg)

                data = await resp.json()
                self._circuit_breaker.record_success()
                return data

        except aiohttp.ClientError as e:
            self._circuit_breaker.record_failure()
            log.warning("rest_client_error", error=str(e))
            raise ConnectionError(str(e)) from e

    async def fetch_candles(
        self,
        symbol: str,
        interval: str = "15m",
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch candle data for a symbol.

        Returns list of candle dicts with keys: T, o, h, l, c, v, n
        """
        coin = self._coin_name(symbol)
        payload: dict[str, Any] = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
            },
        }

        if start_time_ms is not None:
            payload["req"]["startTime"] = start_time_ms
        if end_time_ms is not None:
            payload["req"]["endTime"] = end_time_ms

        result = await self._post(payload)

        if not isinstance(result, list):
            log.warning("rest_candles_unexpected_format", result_type=type(result).__name__)
            return []

        log.debug("rest_candles_fetched", symbol=symbol, count=len(result))
        return result

    async def fetch_candles_paginated(
        self,
        symbol: str,
        interval: str = "15m",
        start_time_ms: int = 0,
        end_time_ms: int | None = None,
        max_candles: int = 50000,
    ) -> list[dict[str, Any]]:
        """Fetch candles with automatic pagination (max 5000 per request)."""
        all_candles: list[dict[str, Any]] = []
        current_start = start_time_ms
        page_size = 5000

        while len(all_candles) < max_candles:
            candles = await self.fetch_candles(
                symbol=symbol,
                interval=interval,
                start_time_ms=current_start,
                end_time_ms=end_time_ms,
            )

            if not candles:
                break

            # Deduplicate by timestamp
            seen = {c["T"] for c in all_candles}
            new_candles = [c for c in candles if c.get("T") not in seen]

            if not new_candles:
                break

            all_candles.extend(new_candles)

            # Advance past last candle
            last_ts = max(c.get("T", 0) for c in new_candles)
            current_start = last_ts + 1

            if len(candles) < page_size:
                break  # No more data

            # Small delay to avoid rate limiting
            await asyncio.sleep(0.2)

        log.info(
            "rest_candles_paginated",
            symbol=symbol,
            total=len(all_candles),
        )
        return all_candles

    async def fetch_dex_meta(self) -> dict[str, Any]:
        """Fetch HIP-3 DEX metadata and asset contexts."""
        return await self._post({
            "type": "metaAndAssetCtxs",
            "dex": self._symbols.dex_prefix,
        })

    async def fetch_all_mids(self) -> dict[str, float]:
        """Fetch current mid prices for all assets."""
        data = await self._post({"type": "allMids"})
        result: dict[str, float] = {}
        if isinstance(data, dict):
            for raw_symbol, price_str in data.items():
                try:
                    symbol = raw_symbol.split(":")[-1] if ":" in raw_symbol else raw_symbol
                    result[symbol] = float(price_str)
                except (ValueError, TypeError):
                    continue
        return result

    async def fetch_user_state(self, wallet_address: str) -> dict[str, Any]:
        """Fetch user account state (positions, margin, etc.)."""
        return await self._post({
            "type": "clearinghouseState",
            "user": wallet_address,
        })

    async def fetch_open_orders(self, wallet_address: str) -> list[dict[str, Any]]:
        """Fetch open orders for a user."""
        result = await self._post({
            "type": "openOrders",
            "user": wallet_address,
        })
        return result if isinstance(result, list) else []
