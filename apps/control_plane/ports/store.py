"""The ProjectStore port · where projects and their agents are remembered.

Deliberately small. The store keeps what the cluster cannot: a project's name,
its status, and the transcript a founder expects to still be there tomorrow.
Live agent counts are *not* here — those come from the runtime, because a
number kept in two places is a number that will eventually disagree.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from apps.control_plane.domain.project import Project
from apps.control_plane.ports.events import Event

MAX_HISTORY_PER_PROJECT = 1000
"""How many events a project keeps.

Part of the contract rather than one adapter's detail: a founder reopening a
project must see the same tail whichever store is behind it, and a demo left
running overnight must not grow without limit.
"""


@runtime_checkable
class ProjectStore(Protocol):
    """Durable state for projects."""

    async def create(self, project: Project) -> Project: ...

    async def get(self, project_id: str) -> Project | None: ...

    async def save(self, project: Project) -> Project:
        """Persist a transition · rename, start, finish."""
        ...

    async def list_active(self) -> list[Project]:
        """Projects that are still open, newest first.

        Used to enforce the concurrent-project limit, so "active" must mean
        exactly "not in a terminal status".
        """
        ...

    async def append_event(self, event: Event) -> None:
        """Record an event for later replay.

        The bus delivers to whoever is watching; this is what a founder sees
        when they come back to a project tomorrow.
        """
        ...

    async def history(self, project_id: str, *, limit: int = 200) -> list[Event]:
        """The tail of a project's events, oldest first."""
        ...

    async def aclose(self) -> None:
        """Release whatever the adapter holds open. Idempotent."""
        ...
