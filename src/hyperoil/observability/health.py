"""HTTP health endpoint for monitoring."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from aiohttp import web

from hyperoil.observability.logger import get_logger

if TYPE_CHECKING:
    from hyperoil.types import HealthStatus

log = get_logger(__name__)

_health_data: dict[str, Any] = {}


def update_health(status: HealthStatus) -> None:
    """Update the health data that will be served."""
    global _health_data
    _health_data = {
        "timestamp_ms": status.timestamp_ms,
        "ws_state": status.ws_state.value,
        "last_tick_ms": status.last_tick_ms,
        "position_open": status.position_open,
        "cycle_status": status.cycle_status.value,
        "current_z": round(status.current_z, 4),
        "regime": status.regime.value,
        "daily_pnl": round(status.daily_pnl, 2),
        "kill_switch_active": status.kill_switch_active,
        "uptime_sec": round(status.uptime_sec, 1),
    }


async def _handle_health(request: web.Request) -> web.Response:
    return web.Response(
        text=json.dumps(_health_data, indent=2),
        content_type="application/json",
    )


async def _handle_kill_switch(request: web.Request) -> web.Response:
    """POST /kill — activate kill switch."""
    _health_data["kill_switch_requested"] = True
    log.warning("kill_switch_requested_via_http")
    return web.Response(text='{"status": "kill_switch_activated"}', content_type="application/json")


async def start_health_server(port: int) -> web.AppRunner:
    """Start the health HTTP server."""
    app = web.Application()
    app.router.add_get("/health", _handle_health)
    app.router.add_post("/kill", _handle_kill_switch)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    log.info("health_server_started", port=port)
    return runner
