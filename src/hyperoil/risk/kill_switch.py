"""Kill switch — multiple mechanisms to emergency-stop the bot.

Supports:
1. File-based: touch a file to activate
2. HTTP-based: POST /kill endpoint (handled by health server)
3. Programmatic: set via code
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hyperoil.observability.logger import get_logger

log = get_logger(__name__)

DEFAULT_KILL_FILE = "data/KILL_SWITCH"


class KillSwitch:
    """Multi-source kill switch for emergency stop."""

    def __init__(self, kill_file_path: str = DEFAULT_KILL_FILE) -> None:
        self._kill_file = Path(kill_file_path)
        self._manual_active: bool = False
        self._http_active: bool = False

    @property
    def is_active(self) -> bool:
        """Check if any kill switch source is active."""
        return self._manual_active or self._http_active or self._file_exists()

    @property
    def reason(self) -> str | None:
        """Return the reason the kill switch is active, or None."""
        if self._manual_active:
            return "manual"
        if self._http_active:
            return "http"
        if self._file_exists():
            return "file"
        return None

    def activate(self, source: str = "manual") -> None:
        """Activate the kill switch programmatically."""
        if source == "http":
            self._http_active = True
        else:
            self._manual_active = True

        log.warning("kill_switch_activated", source=source)

    def deactivate(self) -> None:
        """Deactivate all programmatic kill switches.

        Note: file-based kill switch must be removed manually.
        """
        was_active = self.is_active
        self._manual_active = False
        self._http_active = False

        if was_active:
            log.info("kill_switch_deactivated")

    async def check(self) -> bool:
        """Async check — useful in event loops."""
        return await asyncio.to_thread(lambda: self.is_active)

    def create_kill_file(self) -> None:
        """Create the kill switch file."""
        self._kill_file.parent.mkdir(parents=True, exist_ok=True)
        self._kill_file.write_text("KILL SWITCH ACTIVE\n")
        log.warning("kill_switch_file_created", path=str(self._kill_file))

    def remove_kill_file(self) -> None:
        """Remove the kill switch file if it exists."""
        if self._kill_file.exists():
            self._kill_file.unlink()
            log.info("kill_switch_file_removed", path=str(self._kill_file))

    def _file_exists(self) -> bool:
        return self._kill_file.exists()
