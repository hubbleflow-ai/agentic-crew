"""In-memory ProjectStore.

Honest about what it is: everything vanishes on restart. That is fine for a
laptop demo and for tests, and it keeps Postgres out of the critical path while
the harness itself is what is being taught.

The Postgres adapter that replaces it implements the same port, so nothing
above this line changes when it arrives.
"""

from __future__ import annotations

from collections import defaultdict

from apps.control_plane.domain.project import Project
from apps.control_plane.ports.events import Event

MAX_HISTORY_PER_PROJECT = 1000
"""A demo left running should not grow without limit."""


class InMemoryProjectStore:
    """Satisfies :class:`~apps.control_plane.ports.store.ProjectStore`."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._events: dict[str, list[Event]] = defaultdict(list)

    async def create(self, project: Project) -> Project:
        if project.id in self._projects:
            raise ValueError(f"project {project.id} already exists")
        self._projects[project.id] = project
        return project

    async def get(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    async def save(self, project: Project) -> Project:
        if project.id not in self._projects:
            raise KeyError(f"unknown project {project.id}")
        self._projects[project.id] = project
        return project

    async def list_active(self) -> list[Project]:
        return sorted(
            (p for p in self._projects.values() if not p.status.is_terminal),
            key=lambda p: p.created_at,
            reverse=True,
        )

    async def append_event(self, event: Event) -> None:
        log = self._events[event.project_id]
        log.append(event)
        if len(log) > MAX_HISTORY_PER_PROJECT:
            del log[: len(log) - MAX_HISTORY_PER_PROJECT]

    async def history(self, project_id: str, *, limit: int = 200) -> list[Event]:
        return self._events[project_id][-limit:]
