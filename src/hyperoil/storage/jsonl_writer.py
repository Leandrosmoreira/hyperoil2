"""Async JSONL writer for audit trail and incident logging."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from hyperoil.observability.logger import get_logger
from hyperoil.types import now_ms

log = get_logger(__name__)


class JsonlWriter:
    """Append-only JSONL writer with async buffering."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _get_file_path(self, category: str) -> Path:
        return self._base_dir / f"{category}.jsonl"

    async def write(self, category: str, data: dict[str, Any]) -> None:
        """Write a single record to a category file."""
        record = {"_ts_ms": now_ms(), **data}
        line = json.dumps(record, default=str) + "\n"

        async with self._lock:
            path = self._get_file_path(category)
            with open(path, "a") as f:
                f.write(line)

    async def write_incident(
        self,
        incident_type: str,
        severity: str,
        cycle_id: str | None = None,
        **details: Any,
    ) -> None:
        """Write an incident record."""
        await self.write("incidents", {
            "type": incident_type,
            "severity": severity,
            "cycle_id": cycle_id,
            **details,
        })

    async def write_trade(self, **trade_data: Any) -> None:
        """Write a trade record."""
        await self.write("trades", trade_data)

    async def write_signal(self, **signal_data: Any) -> None:
        """Write a signal event."""
        await self.write("signals", signal_data)
