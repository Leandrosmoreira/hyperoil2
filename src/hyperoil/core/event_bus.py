"""Simple async event bus for internal pub/sub."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

from hyperoil.observability.logger import get_logger

log = get_logger(__name__)

# Type for async event handlers
EventHandler = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Async pub/sub for internal events. Fire-and-forget semantics."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event: str, handler: EventHandler) -> None:
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: EventHandler) -> None:
        if event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    async def emit(self, event: str, **kwargs: Any) -> None:
        """Emit an event to all subscribers. Errors in handlers are logged, not raised."""
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            try:
                await handler(**kwargs)
            except Exception:
                log.exception("event_handler_error", event=event, handler=handler.__name__)

    def emit_nowait(self, event: str, **kwargs: Any) -> None:
        """Fire-and-forget emit. Creates a task, does not block."""
        loop = asyncio.get_running_loop()
        loop.create_task(self.emit(event, **kwargs))
