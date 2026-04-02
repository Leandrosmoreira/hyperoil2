"""Tests for kill switch."""

from __future__ import annotations

import pytest

from hyperoil.risk.kill_switch import KillSwitch


class TestKillSwitch:
    def test_initially_inactive(self, tmp_path) -> None:
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        assert not ks.is_active
        assert ks.reason is None

    def test_manual_activation(self, tmp_path) -> None:
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        ks.activate("manual")
        assert ks.is_active
        assert ks.reason == "manual"

    def test_http_activation(self, tmp_path) -> None:
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        ks.activate("http")
        assert ks.is_active
        assert ks.reason == "http"

    def test_file_activation(self, tmp_path) -> None:
        kill_file = tmp_path / "KILL"
        ks = KillSwitch(kill_file_path=str(kill_file))

        assert not ks.is_active
        kill_file.write_text("ACTIVE")
        assert ks.is_active
        assert ks.reason == "file"

    def test_deactivate(self, tmp_path) -> None:
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        ks.activate("manual")
        ks.activate("http")
        assert ks.is_active

        ks.deactivate()
        assert not ks.is_active

    def test_file_survives_deactivate(self, tmp_path) -> None:
        kill_file = tmp_path / "KILL"
        kill_file.write_text("ACTIVE")
        ks = KillSwitch(kill_file_path=str(kill_file))

        ks.deactivate()
        # File still exists — must be removed manually
        assert ks.is_active
        assert ks.reason == "file"

    def test_create_kill_file(self, tmp_path) -> None:
        kill_file = tmp_path / "subdir" / "KILL"
        ks = KillSwitch(kill_file_path=str(kill_file))

        ks.create_kill_file()
        assert kill_file.exists()
        assert ks.is_active

    def test_remove_kill_file(self, tmp_path) -> None:
        kill_file = tmp_path / "KILL"
        kill_file.write_text("ACTIVE")
        ks = KillSwitch(kill_file_path=str(kill_file))

        assert ks.is_active
        ks.remove_kill_file()
        assert not ks.is_active

    @pytest.mark.asyncio
    async def test_async_check(self, tmp_path) -> None:
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        assert not await ks.check()

        ks.activate()
        assert await ks.check()

    def test_priority_manual_over_http(self, tmp_path) -> None:
        """Manual activation takes priority in reason reporting."""
        ks = KillSwitch(kill_file_path=str(tmp_path / "KILL"))
        ks.activate("manual")
        ks.activate("http")
        # Manual is checked first
        assert ks.reason == "manual"
