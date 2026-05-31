"""Simple asyncio pub/sub event bus.

Events:
    agent.completed, agent.failed, agent.flag_human,
    task.ready, validation.complete, project.phase_done
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Union

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Union[None, Awaitable[None]]]

KNOWN_EVENTS = frozenset({
    "agent.completed",
    "agent.failed",
    "agent.flag_human",
    "task.ready",
    "validation.complete",
    "project.phase_done",
    "observability.anomaly",
})


class EventBus:
    """In-process async event bus.

    Handlers may be sync or async. Async handlers are awaited concurrently.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        if event not in KNOWN_EVENTS:
            logger.warning("event_bus.unknown_event_subscribed", extra={"event": event})
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        if handler in self._handlers.get(event, []):
            self._handlers[event].remove(handler)

    async def publish(self, event: str, data: Any = None) -> None:
        handlers = list(self._handlers.get(event, []))
        if not handlers:
            return
        coros = []
        for h in handlers:
            try:
                ret = h(data)
                if inspect.isawaitable(ret):
                    coros.append(ret)
            except Exception:
                logger.exception("event_bus.sync_handler_error", extra={"event": event})
        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(
                        "event_bus.async_handler_error",
                        extra={"event": event, "error": str(r)},
                    )
