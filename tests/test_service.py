"""The whole spawn path, exercised with fakes · no cluster, no Redis."""

from dataclasses import dataclass

import pytest
from apps.control_plane.adapters.fake_runtime import FakeAgentRuntime
from apps.control_plane.adapters.memory_store import InMemoryProjectStore
from apps.control_plane.domain.caps import MAX_CONCURRENT_PROJECTS, PER_PROJECT_LIMITS, AgentRole
from apps.control_plane.domain.project import PROVISIONAL_NAME, ProjectStatus
from apps.control_plane.ports.events import Event, EventKind
from apps.control_plane.service import CapExceeded, CrewService


class RecordingBus:
    """A bus with the recorder already attached.

    In production those are two pieces: agents publish onto Redis, and one
    subscriber in the control plane writes what it hears into the store. Here
    they collapse into one synchronous object, so a test sees the same end
    state without running a background task.
    """

    def __init__(self, store: InMemoryProjectStore) -> None:
        self.events: list[Event] = []
        self._store = store

    async def publish(self, event: Event) -> None:
        self.events.append(event)
        await self._store.append_event(event)  # what record() does

    def subscribe(self, project_id: str):  # noqa: ANN201 · unused by these tests
        raise NotImplementedError

    def kinds(self) -> list[EventKind]:
        return [e.kind for e in self.events]


@dataclass
class Crew:
    """A whole control plane, assembled from fakes."""

    service: CrewService
    runtime: FakeAgentRuntime
    bus: RecordingBus
    store: InMemoryProjectStore


@pytest.fixture
def crew() -> Crew:
    store = InMemoryProjectStore()
    runtime, bus = FakeAgentRuntime(), RecordingBus(store)
    return Crew(
        service=CrewService(store=store, runtime=runtime, events=bus),
        runtime=runtime,
        bus=bus,
        store=store,
    )


class TestOpeningAProject:
    @pytest.mark.asyncio
    async def test_starts_unnamed(self, crew: Crew) -> None:
        project = await crew.service.open_project("hi")
        assert project.name == PROVISIONAL_NAME
        assert not project.is_named

    @pytest.mark.asyncio
    async def test_puts_an_em_on_it_and_nobody_else(self, crew: Crew) -> None:
        await crew.service.open_project("Add a /healthz endpoint")
        assert [s.role for s in crew.runtime.launched] == [AgentRole.ENGINEERING_MANAGER]
        assert crew.runtime.launched[0].assignment == "Add a /healthz endpoint"

    @pytest.mark.asyncio
    async def test_founder_message_reaches_the_bus_before_the_agent(self, crew: Crew) -> None:
        await crew.service.open_project("hi")
        assert crew.bus.kinds() == [EventKind.FOUNDER_MESSAGE, EventKind.AGENT_SPAWNED]

    @pytest.mark.asyncio
    async def test_a_project_with_an_agent_is_running(self, crew: Crew) -> None:
        project = await crew.service.open_project("hi")
        assert (await crew.store.get(project.id)).status is ProjectStatus.RUNNING

    @pytest.mark.asyncio
    async def test_the_returned_project_matches_the_stored_one(self, crew: Crew) -> None:
        """The response is built from what was returned, not what was stored."""
        returned = await crew.service.open_project("hi")
        assert returned == await crew.store.get(returned.id)

    @pytest.mark.asyncio
    async def test_refuses_a_fourth_concurrent_project(self, crew: Crew) -> None:
        for _ in range(MAX_CONCURRENT_PROJECTS):
            await crew.service.open_project("work")
        with pytest.raises(CapExceeded) as exc:
            await crew.service.open_project("one more")
        assert exc.value.refusal.scope == "projects"

    @pytest.mark.asyncio
    async def test_finishing_one_makes_room(self, crew: Crew) -> None:
        projects = [await crew.service.open_project("work") for _ in range(MAX_CONCURRENT_PROJECTS)]
        await crew.service.finish_project(projects[0].id)
        assert await crew.service.open_project("room now")  # no raise


class TestNaming:
    @pytest.mark.asyncio
    async def test_rename_is_persisted_and_announced(self, crew: Crew) -> None:
        project = await crew.service.open_project("add healthz")
        await crew.service.rename_project(project.id, "Healthz endpoint")

        assert (await crew.store.get(project.id)).name == "Healthz endpoint"
        assert EventKind.PROJECT_RENAMED in crew.bus.kinds()


class TestSpawnCaps:
    @pytest.mark.asyncio
    async def test_refusal_is_surfaced_not_swallowed(self, crew: Crew) -> None:
        project = await crew.service.open_project("build it")
        with pytest.raises(CapExceeded):
            # The EM cap is 1, and open_project already used it.
            await crew.service.spawn(project.id, AgentRole.ENGINEERING_MANAGER)
        assert EventKind.SPAWN_REFUSED in crew.bus.kinds()

    @pytest.mark.asyncio
    async def test_override_lifts_the_project_cap(self, crew: Crew) -> None:
        project = await crew.service.open_project("build it")
        for _ in range(PER_PROJECT_LIMITS[AgentRole.BACKEND_ENGINEER]):
            await crew.service.spawn(project.id, AgentRole.BACKEND_ENGINEER)
        with pytest.raises(CapExceeded):
            await crew.service.spawn(project.id, AgentRole.BACKEND_ENGINEER)
        await crew.service.spawn(project.id, AgentRole.BACKEND_ENGINEER, override=True)
        launched = sum(s.role is AgentRole.BACKEND_ENGINEER for s in crew.runtime.launched)
        assert launched == PER_PROJECT_LIMITS[AgentRole.BACKEND_ENGINEER] + 1

    @pytest.mark.asyncio
    async def test_census_comes_from_the_runtime_not_a_counter(self, crew: Crew) -> None:
        """A control plane that restarts must not forget running agents."""
        project = await crew.service.open_project("build it")
        await crew.service.spawn(project.id, AgentRole.BACKEND_ENGINEER)

        # A brand-new service over the same runtime · nothing carried in memory.
        restarted = CrewService(
            store=crew.store, runtime=crew.runtime, events=crew.bus
        )
        # The first spawn above already used part of the cap; the restarted
        # service must see it, so only the remainder is allowed.
        remaining = PER_PROJECT_LIMITS[AgentRole.BACKEND_ENGINEER] - 1
        for _ in range(remaining):
            await restarted.spawn(project.id, AgentRole.BACKEND_ENGINEER)
        with pytest.raises(CapExceeded):
            await restarted.spawn(project.id, AgentRole.BACKEND_ENGINEER)


class TestHistory:
    @pytest.mark.asyncio
    async def test_replays_what_was_broadcast(self, crew: Crew) -> None:
        project = await crew.service.open_project("hi")
        replay = await crew.store.history(project.id)
        assert [e.kind for e in replay] == crew.bus.kinds()


class TestTokenBudget:
    """The ceiling that is denominated in money rather than in turns.

    Every other limit here is per-turn or per-agent, and bounds nothing on its
    own: agents answer each other, every reply is a fresh turn, and the
    per-turn model-call ceiling resets each time. One project reached 70M
    tokens in twenty minutes with every one of those limits intact.
    """

    @pytest.mark.asyncio
    async def test_usage_under_the_budget_changes_nothing(self, crew: Crew) -> None:
        project = await crew.service.open_project("build it")
        await crew.service._charge(
            Event(
                project_id=project.id,
                kind=EventKind.USAGE,
                source="backend_engineer/x",
                payload={"input_tokens": 10, "output_tokens": 5},
            )
        )
        assert crew.runtime.launched, "agents should still be running"
        assert not (await crew.service.get_project(project.id)).status.is_terminal

    @pytest.mark.asyncio
    async def test_passing_the_budget_stops_every_agent(
        self, crew: Crew, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("apps.control_plane.service.MAX_PROJECT_TOKENS", 100)
        project = await crew.service.open_project("build it")

        await crew.service._charge(
            Event(
                project_id=project.id,
                kind=EventKind.USAGE,
                source="backend_engineer/x",
                payload={"input_tokens": 90, "output_tokens": 20},
            )
        )

        assert await crew.runtime.census(AgentRole.ENGINEERING_MANAGER, project.id) == 0
        assert (await crew.service.get_project(project.id)).status is ProjectStatus.CANCELLED
        assert EventKind.ERROR in crew.bus.kinds()

    @pytest.mark.asyncio
    async def test_it_stops_once_not_once_per_event(
        self, crew: Crew, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A burst of usage events must not each fire their own teardown."""
        monkeypatch.setattr("apps.control_plane.service.MAX_PROJECT_TOKENS", 10)
        project = await crew.service.open_project("build it")
        over = Event(
            project_id=project.id,
            kind=EventKind.USAGE,
            source="backend_engineer/x",
            payload={"input_tokens": 50, "output_tokens": 0},
        )

        for _ in range(3):
            await crew.service._charge(over)

        errors = [k for k in crew.bus.kinds() if k is EventKind.ERROR]
        assert len(errors) == 1
