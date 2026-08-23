"""Use cases · where the rules meet the world.

This is the only layer that knows both. :mod:`domain` holds rules and touches
nothing; :mod:`adapters` touch everything and hold no rules. A use case here
reads state through a port, asks the domain what is allowed, and writes the
result back through a port.

Nothing in this file imports Kubernetes, Redis, or FastAPI. It is written
against the ports, which is why the tests for it run in milliseconds against
fakes and still exercise the real decision logic.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from agents.shared.logging_setup import bind_context, setup_logging
from apps.control_plane.domain.caps import (
    AgentRole,
    Census,
    Refusal,
    check_new_project,
    check_spawn,
)
from apps.control_plane.domain.project import Project, ProjectStatus
from apps.control_plane.ports.events import Event, EventBus, EventKind
from apps.control_plane.ports.runtime import AgentHandle, AgentRuntime, AgentSpec
from apps.control_plane.ports.store import ProjectStore

log = setup_logging("crew-service")

FIRST_ROLE = AgentRole.ENGINEERING_MANAGER
"""Who a new project starts with.

The EM is spawned first and is the only role holding `spawn_agent`, so the
crew's shape is decided by an agent that has seen the request — not by the
founder guessing which specialists they will need.
"""


class CapExceeded(Exception):
    """A spawn or project creation was refused by policy."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class SpawnResult:
    handle: AgentHandle
    project: Project


class CrewService:
    """Everything the crew can be asked to do."""

    def __init__(
        self,
        *,
        store: ProjectStore,
        runtime: AgentRuntime,
        events: EventBus,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._events = events

    # ─── projects ────────────────────────────────────────────────────────

    async def open_project(self, request: str) -> Project:
        """Start a project and put an EM on it.

        The project is *not* named here. The opening message is as often "hi"
        as it is a requirement, and a chat titled "hi" helps nobody · the EM
        renames it once it has scoped something concrete.
        """
        active = await self._store.list_active()
        refusal = check_new_project(len(active))
        if refusal:
            raise CapExceeded(refusal)

        project = await self._store.create(Project(id=f"proj-{secrets.token_hex(4)}"))
        bind_context(project_id=project.id)
        log.info("project.opened project_id=%s active=%d", project.id, len(active) + 1)

        await self._emit(
            Event(
                project_id=project.id,
                kind=EventKind.FOUNDER_MESSAGE,
                source="founder",
                payload={"text": request},
            )
        )
        # Return what spawn produced, not the snapshot taken before it · the
        # project is RUNNING the moment the EM exists, and handing back the
        # older instance told the founder it was still being scoped.
        result = await self.spawn(project.id, FIRST_ROLE, assignment=request)
        return result.project

    async def get_project(self, project_id: str) -> Project:
        """Look one up · raises ``KeyError`` if it is not there."""
        return await self._require(project_id)

    async def list_active(self) -> list[Project]:
        return await self._store.list_active()

    async def rename_project(self, project_id: str, name: str) -> Project:
        """Give a project its real name · called by the EM, once."""
        project = await self._require(project_id)
        renamed = await self._store.save(project.rename(name))
        log.info("project.renamed project_id=%s name=%s", project_id, renamed.name)
        await self._emit(
            Event(
                project_id=project_id,
                kind=EventKind.PROJECT_RENAMED,
                source=FIRST_ROLE.value,
                payload={"name": renamed.name},
            )
        )
        return renamed

    async def finish_project(self, project_id: str, *, cancelled: bool = False) -> Project:
        project = await self._require(project_id)
        return await self._store.save(project.finish(cancelled=cancelled))

    # ─── agents ──────────────────────────────────────────────────────────

    async def spawn(
        self,
        project_id: str,
        role: AgentRole,
        *,
        assignment: str = "",
        override: bool = False,
    ) -> SpawnResult:
        """Add one specialist to a project, if the caps allow it.

        The census is asked of the runtime, not remembered here. A control
        plane that restarts mid-project must not forget the eight agents it
        already has running.
        """
        project = await self._require(project_id)

        census = Census(
            role=role,
            in_project=await self._runtime.census(role, project_id),
            everywhere=await self._runtime.census(role),
        )
        refusal = check_spawn(census, override=override)
        if refusal:
            log.warning(
                "spawn.refused project_id=%s role=%s scope=%s current=%d limit=%d",
                project_id,
                role,
                refusal.scope,
                refusal.current,
                refusal.limit,
            )
            # Surfaced, not swallowed · a refusal the founder never sees looks
            # like an agent that quietly decided not to work.
            await self._emit(
                Event(
                    project_id=project_id,
                    kind=EventKind.SPAWN_REFUSED,
                    source="control-plane",
                    payload={
                        "role": role.value,
                        "scope": refusal.scope,
                        "limit": refusal.limit,
                        "message": refusal.message,
                    },
                )
            )
            raise CapExceeded(refusal)

        handle = await self._runtime.launch(
            AgentSpec(role=role, project_id=project_id, assignment=assignment)
        )
        await self._emit(
            Event(
                project_id=project_id,
                kind=EventKind.AGENT_SPAWNED,
                source="control-plane",
                payload={"role": role.value, "agent": handle.name},
            )
        )

        if project.status is ProjectStatus.SCOPING:
            # A project with someone working on it is no longer being scoped.
            project = await self._store.save(project.start())

        return SpawnResult(handle=handle, project=project)

    async def roster(self, project_id: str) -> list[dict[str, Any]]:
        """Who is on this project right now, and how each one is doing.

        Read from the runtime rather than a table · the cluster is the only
        thing that knows an agent crashed thirty seconds ago.
        """
        roster: list[dict[str, Any]] = []
        for role in AgentRole:
            for handle in await self._runtime.handles(role, project_id):
                status = await self._runtime.status(handle)
                roster.append(
                    {
                        "name": handle.name,
                        "role": role.value,
                        "state": status.state.value,
                        "reason": status.reason,
                        "stuck": status.is_stuck,
                    }
                )
        return roster

    # ─── conversation ────────────────────────────────────────────────────

    async def founder_says(self, project_id: str, text: str) -> None:
        """A follow-up from the founder, delivered to everyone on the project."""
        await self._emit(
            Event(
                project_id=project_id,
                kind=EventKind.FOUNDER_MESSAGE,
                source="founder",
                payload={"text": text},
            )
        )

    async def escalate(
        self,
        project_id: str,
        *,
        question: str,
        context: str = "",
        options: list[str] | None = None,
    ) -> None:
        """Surface an agent's decision to the founder."""
        log.info("escalation.raised project_id=%s q=%s", project_id, question[:80])
        await self._emit(
            Event(
                project_id=project_id,
                kind=EventKind.ESCALATION,
                source="control-plane",
                payload={
                    "question": question,
                    "context": context,
                    "options": options or [],
                },
            )
        )

    async def history(self, project_id: str, *, limit: int = 200) -> list[Event]:
        return await self._store.history(project_id, limit=limit)

    def watch(self, project_id: str) -> AsyncIterator[Event]:
        """The live feed · used by the WebSocket route."""
        return self._events.subscribe(project_id)

    # ─── internals ───────────────────────────────────────────────────────

    async def _require(self, project_id: str) -> Project:
        project = await self._store.get(project_id)
        if project is None:
            raise KeyError(project_id)
        return project

    async def _emit(self, event: Event) -> None:
        """Publish onto the bus. Recording is somebody else's job.

        Deliberately not written to the store here. Agents publish from inside
        their own pods and cannot reach the store at all, so if each publisher
        also recorded, the history would hold control-plane events and nothing
        else. :meth:`record` is the single writer, and it sees both.
        """
        await self._events.publish(event)

    async def record(self) -> None:
        """Write every event on the bus into the store. Runs forever.

        Started once at boot. This is what makes a project a founder reopens
        tomorrow read the same as the one they watched today.
        """
        async for event in self._events.subscribe_all():
            try:
                await self._store.append_event(event)
            except Exception:
                # A history gap is bad; a recorder that dies and leaves every
                # later event unrecorded is worse.
                log.exception("recorder.append_failed project_id=%s", event.project_id)
