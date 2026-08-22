"""Redis adapter for the EventBus port.

Pub/sub, not a stream: events are for whoever is watching *now*. A founder who
opens the UI mid-project wants the project's history from the store, which is
durable, rather than a replay of every tool call from a log that was never
meant to be one.

One channel per project, so three concurrent projects do not deliver each
other's traffic to three browser tabs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

import redis.asyncio as redis
from agents.shared.logging_setup import setup_logging
from apps.control_plane.ports.events import Event, EventKind
from redis.exceptions import TimeoutError as RedisTimeoutError

log = setup_logging("redis-events")

IDLE_TIMEOUT_S = 5.0
"""How long a read waits before looping. Not a disconnect · a quiet project is
the normal case while a model is thinking."""


def channel_for(project_id: str) -> str:
    return f"crew/project/{project_id}/events"


class RedisEventBus:
    """Satisfies :class:`~apps.control_plane.ports.events.EventBus`."""

    def __init__(self, url: str = "redis://redis:6379/0") -> None:
        self._redis: redis.Redis = redis.from_url(url)

    async def publish(self, event: Event) -> None:
        await self._redis.publish(channel_for(event.project_id), json.dumps(asdict(event)))
        log.debug("event.published kind=%s project_id=%s", event.kind, event.project_id)

    async def subscribe(self, project_id: str) -> AsyncIterator[Event]:
        """Yield this project's events until the caller stops iterating.

        The subscription is torn down in ``finally`` · a browser tab closing
        mid-iteration otherwise leaves a subscriber attached to Redis forever.
        """
        pubsub = self._redis.pubsub()
        channel = channel_for(project_id)
        await pubsub.subscribe(channel)
        log.info("event.subscribed project_id=%s", project_id)
        try:
            while True:
                try:
                    raw = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=IDLE_TIMEOUT_S
                    )
                except RedisTimeoutError:
                    continue
                if raw is None or raw.get("type") != "message":
                    continue
                yield _decode(raw["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
            log.info("event.unsubscribed project_id=%s", project_id)

    async def aclose(self) -> None:
        await self._redis.aclose()


def _decode(data: Any) -> Event:
    payload = json.loads(data.decode() if isinstance(data, bytes) else data)
    return Event(
        project_id=payload["project_id"],
        kind=EventKind(payload["kind"]),
        source=payload["source"],
        payload=payload.get("payload", {}),
        at=payload["at"],
    )
