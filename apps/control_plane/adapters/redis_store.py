"""Redis adapter for the ProjectStore port · state that survives a restart.

The control plane already cannot start without Redis — the event bus is there —
so putting projects there too adds no new dependency, no schema and no
migration step. A pod that is rescheduled at 3am comes back to the same
projects, the same names and the same transcript.

Durability is a property of the *server*, not of this file: Redis must be run
with an append-only file and a volume behind it, which is what
``deploy/10-redis.yaml`` now does. Point this at a Redis started with
``--appendonly no`` and you have the in-memory store with extra hops.

The layout is three keys, deliberately readable with ``redis-cli`` while a
demo is running:

``crew:project:<id>``            JSON · one project
``crew:projects:active``         sorted set · id → created_at, newest first
``crew:project:<id>:history``    list · JSON events, oldest first, capped

Active projects are an *index*, not a scan. ``list_active`` is on the path of
every new project (it enforces the concurrency cap), and ``KEYS``/``SCAN`` on
that path is the thing that makes a Redis-backed service slow later, once
someone has left a hundred finished projects lying around.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import redis.asyncio as redis
from agents.shared.logging_setup import setup_logging
from apps.control_plane.domain.project import Project, ProjectStatus
from apps.control_plane.ports.events import Event, EventKind
from apps.control_plane.ports.store import MAX_HISTORY_PER_PROJECT

log = setup_logging("redis-store")

# redis-py types every read as `bytes | str` because `decode_responses` is a
# runtime flag it cannot see in the signature. The client below is built with
# it on, so the casts say what is already true rather than hiding anything.

DEFAULT_PREFIX = "crew"
"""Namespace for every key this adapter owns · tests pass their own."""


class RedisProjectStore:
    """Satisfies :class:`~apps.control_plane.ports.store.ProjectStore`."""

    def __init__(
        self,
        url: str = "redis://redis:6379/0",
        *,
        prefix: str = DEFAULT_PREFIX,
    ) -> None:
        self._redis: redis.Redis = redis.from_url(url, decode_responses=True)
        self._prefix = prefix

    # ─── keys ────────────────────────────────────────────────────────────

    def _project_key(self, project_id: str) -> str:
        return f"{self._prefix}:project:{project_id}"

    def _history_key(self, project_id: str) -> str:
        return f"{self._prefix}:project:{project_id}:history"

    @property
    def _active_key(self) -> str:
        return f"{self._prefix}:projects:active"

    # ─── projects ────────────────────────────────────────────────────────

    async def create(self, project: Project) -> Project:
        """Write a new project, refusing to overwrite one that exists.

        ``NX`` rather than a read-then-write: two control-plane replicas
        opening projects at the same moment would both pass the read.
        """
        written = await self._redis.set(
            self._project_key(project.id), _encode_project(project), nx=True
        )
        if not written:
            raise ValueError(f"project {project.id} already exists")
        await self._redis.zadd(self._active_key, {project.id: project.created_at.timestamp()})
        return project

    async def get(self, project_id: str) -> Project | None:
        raw = cast("str | None", await self._redis.get(self._project_key(project_id)))
        return _decode_project(raw) if raw else None

    async def save(self, project: Project) -> Project:
        """Persist a transition · rename, start, finish.

        ``XX`` so a save can never conjure a project the store never had; the
        service treats an unknown id as a caller bug, not as a create.
        """
        written = await self._redis.set(
            self._project_key(project.id), _encode_project(project), xx=True
        )
        if not written:
            raise KeyError(f"unknown project {project.id}")

        # Leaving the index is what "finished" means here. Filtering terminal
        # projects out on read instead would make the cap check pay for every
        # project the crew has ever run.
        if project.status.is_terminal:
            await self._redis.zrem(self._active_key, project.id)
        return project

    async def list_active(self) -> list[Project]:
        ids = cast("list[str]", await self._redis.zrevrange(self._active_key, 0, -1))
        if not ids:
            return []

        raws = cast(
            "list[str | None]",
            await self._redis.mget([self._project_key(project_id) for project_id in ids]),
        )
        projects: list[Project] = []
        for project_id, raw in zip(ids, raws, strict=True):
            if raw is None:
                # The index outlived the project · only reachable if someone
                # deleted a key by hand. Heal rather than raise.
                await self._redis.zrem(self._active_key, project_id)
                log.warning("store.index_stale project_id=%s", project_id)
                continue
            project = _decode_project(raw)
            if not project.status.is_terminal:
                projects.append(project)
        return projects

    # ─── history ─────────────────────────────────────────────────────────

    async def append_event(self, event: Event) -> None:
        """Append to the project's log, capped, in one round trip."""
        key = self._history_key(event.project_id)
        pipe = self._redis.pipeline()
        pipe.rpush(key, _encode_event(event))
        pipe.ltrim(key, -MAX_HISTORY_PER_PROJECT, -1)
        await pipe.execute()

    async def history(self, project_id: str, *, limit: int = 200) -> list[Event]:
        raw = cast(
            "list[str]", await self._redis.lrange(self._history_key(project_id), -limit, -1)
        )
        return [_decode_event(item) for item in raw]

    async def aclose(self) -> None:
        await self._redis.aclose()


# ─── wire format ─────────────────────────────────────────────────────────
#
# Written by hand rather than with `dataclasses.asdict` so the stored shape is
# a decision rather than a consequence: a field renamed in the domain then
# fails a test here instead of silently orphaning every project already in
# Redis.


def _encode_project(project: Project) -> str:
    return json.dumps(
        {
            "id": project.id,
            "name": project.name,
            "status": project.status.value,
            "created_at": project.created_at.isoformat(),
            "renamed_at": project.renamed_at.isoformat() if project.renamed_at else None,
        }
    )


def _decode_project(raw: str) -> Project:
    payload: dict[str, Any] = json.loads(raw)
    return Project(
        id=payload["id"],
        name=payload["name"],
        status=ProjectStatus(payload["status"]),
        created_at=_parse_time(payload["created_at"]),
        renamed_at=_parse_time(payload["renamed_at"]) if payload["renamed_at"] else None,
    )


def _encode_event(event: Event) -> str:
    return json.dumps(
        {
            "project_id": event.project_id,
            "kind": event.kind.value,
            "source": event.source,
            "payload": event.payload,
            "at": event.at,
            "to": event.to,
        }
    )


def _decode_event(raw: str) -> Event:
    payload: dict[str, Any] = json.loads(raw)
    return Event(
        project_id=payload["project_id"],
        kind=EventKind(payload["kind"]),
        source=payload["source"],
        payload=payload.get("payload", {}),
        at=payload["at"],
        to=payload.get("to", ""),
    )


def _parse_time(value: str) -> datetime:
    """Read a timestamp back as an aware UTC datetime.

    A naive datetime survives the round trip through JSON and then fails much
    later, when it is compared with an aware one.
    """
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
