"""In-memory ProjectStore · the one the tests run against.

Everything vanishes on restart, which is exactly what a test wants and exactly
what a founder does not. The control plane runs on
:class:`~apps.control_plane.adapters.redis_store.RedisProjectStore`; this one
exists so the whole service can be assembled in a test in microseconds with no
server anywhere.

Both implement the same port, and the same contract test runs against both.
"""

from __future__ import annotations

from collections import defaultdict

from apps.control_plane.domain.project import Project
from apps.control_plane.ports.events import Event
from apps.control_plane.ports.store import MAX_HISTORY_PER_PROJECT


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

    async def aclose(self) -> None:
        """Nothing is held open · here to satisfy the port."""
