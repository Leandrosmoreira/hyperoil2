"""Multi-asset Donchian signal engine.

Maintains a per-symbol bounded buffer of recent bars and produces a
``DonchianSignal`` for the latest bar of each symbol on demand.

Designed to be used in two modes:
  - Live: ``update_bar(symbol, bar)`` once per closed 4h bar, then
    ``compute_signal(symbol)`` (or ``compute_all()``) to read the signal.
  - Warmup / batch: ``seed_history(symbol, bars)`` bulk-loads historical bars
    into the buffer, then the same compute methods work.

The buffer is sized to ``max(lookbacks) + headroom`` so the largest channel
can be computed without losing the "+1 bar excluded from window" guarantee.
EMA(period) is recomputed from scratch over the buffer on each call — for
default lookbacks (max 2160) and EMA period 200 this is well under 1 ms per
symbol on modern hardware, so no incremental EMA state is needed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from hyperoil.donchian.signals.donchian_channel import compute_channel  # noqa: F401
from hyperoil.donchian.signals.ensemble import compute_ensemble
from hyperoil.donchian.signals.regime_ema import compute_ema, entry_allowed
from hyperoil.donchian.types import (
    LOOKBACKS_4H,
    DonchianChannel,
    DonchianSignal,
)

# Headroom over max lookback to keep the "current bar excluded" guarantee
# even after a few stale bars get pushed out the back of the deque.
_BUFFER_HEADROOM = 50


@dataclass
class _Bar:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float


class DonchianSignalEngine:
    """Stateful per-symbol Donchian signal engine."""

    def __init__(
        self,
        lookbacks: list[int] | None = None,
        ema_period: int = 200,
        min_score_entry: float = 0.33,
        buffer_size: int | None = None,
    ) -> None:
        self.lookbacks = sorted(lookbacks if lookbacks is not None else LOOKBACKS_4H)
        self.ema_period = ema_period
        self.min_score_entry = min_score_entry
        self.buffer_size = buffer_size or (max(self.lookbacks) + _BUFFER_HEADROOM)
        self._buffers: dict[str, deque[_Bar]] = {}

    # ------------------------------------------------------------------
    # Buffer management
    # ------------------------------------------------------------------
    def _ensure_buffer(self, symbol: str) -> deque[_Bar]:
        buf = self._buffers.get(symbol)
        if buf is None:
            buf = deque(maxlen=self.buffer_size)
            self._buffers[symbol] = buf
        return buf

    def seed_history(self, symbol: str, bars: list[dict]) -> None:
        """Bulk-load historical bars into the buffer.

        Older bars beyond ``buffer_size`` are silently discarded by the deque.
        Each bar is a dict with keys: timestamp_ms, open, high, low, close.
        """
        buf: deque[_Bar] = deque(maxlen=self.buffer_size)
        for b in bars:
            buf.append(
                _Bar(
                    timestamp_ms=int(b["timestamp_ms"]),
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                )
            )
        self._buffers[symbol] = buf

    def update_bar(self, symbol: str, bar: dict) -> None:
        """Append one freshly closed bar to the per-symbol buffer."""
        buf = self._ensure_buffer(symbol)
        buf.append(
            _Bar(
                timestamp_ms=int(bar["timestamp_ms"]),
                open=float(bar["open"]),
                high=float(bar["high"]),
                low=float(bar["low"]),
                close=float(bar["close"]),
            )
        )

    def n_bars(self, symbol: str) -> int:
        return len(self._buffers.get(symbol, ()))

    def is_warm(self, symbol: str) -> bool:
        """A symbol is warm when it has enough bars to compute the LARGEST
        channel (with the current bar excluded). Default lookbacks max at
        2160, far more than the 3*EMA_period needed for EMA stability."""
        return self.n_bars(symbol) >= max(self.lookbacks) + 1

    def symbols(self) -> list[str]:
        return list(self._buffers.keys())

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------
    def compute_signal(self, symbol: str) -> DonchianSignal | None:
        """Compute the latest signal for one symbol.

        Returns ``None`` if the symbol's buffer is not yet warm.
        """
        if not self.is_warm(symbol):
            return None

        buf = self._buffers[symbol]
        bars = list(buf)
        highs = np.fromiter((b.high for b in bars), dtype=float, count=len(bars))
        lows = np.fromiter((b.low for b in bars), dtype=float, count=len(bars))
        closes = np.fromiter((b.close for b in bars), dtype=float, count=len(bars))
        last = bars[-1]

        ens = compute_ensemble(highs, lows, closes, self.lookbacks)
        ema = compute_ema(closes, self.ema_period)
        valid = entry_allowed(
            close=float(last.close),
            ema=ema,
            score=ens.score,
            min_score=self.min_score_entry,
        )

        # Stop reference is the mid of the dominant (longest breaking-out)
        # channel. When score=0 there is no dominant — fall back to the
        # smallest lookback's mid as a placeholder; the decision engine will
        # not enter on score=0 anyway, so this never gates a real trade.
        if ens.dominant_lookback > 0:
            stop_line = next(
                ch.mid for ch in ens.channels if ch.lookback == ens.dominant_lookback
            )
        else:
            stop_line = ens.channels[0].mid

        return DonchianSignal(
            symbol=symbol,
            timestamp_ms=last.timestamp_ms,
            score=ens.score,
            dominant_lookback=ens.dominant_lookback,
            stop_line=stop_line,
            ema_200=ema,
            entry_valid=valid,
            channels=[
                DonchianChannel(
                    lookback=ch.lookback,
                    upper=ch.upper,
                    lower=ch.lower,
                    mid=ch.mid,
                )
                for ch in ens.channels
            ],
        )

    def compute_all(self) -> dict[str, DonchianSignal]:
        """Compute the latest signal for every symbol that is currently warm.

        Symbols that are not warm yet are silently omitted.
        """
        out: dict[str, DonchianSignal] = {}
        for sym in self._buffers:
            sig = self.compute_signal(sym)
            if sig is not None:
                out[sym] = sig
        return out
