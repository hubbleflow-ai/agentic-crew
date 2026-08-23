"""One contract, both stores.

The point of a port is that the thing above it cannot tell which adapter is
underneath. That is only true if it is checked, so every test here runs twice:
once against the in-memory store the rest of the suite uses, and once against
real Redis.

The Redis half skips when no server is reachable — `pytest` on a laptop with
nothing running still passes, and CI with Redis up tests the real thing.
Nothing here is mocked; a fake Redis would only prove that the fake agrees
with itself.
"""

import os
import uuid

import pytest
import redis.asyncio as redis
from apps.control_plane.adapters.memory_store import InMemoryProjectStore
from apps.control_plane.adapters.redis_store import RedisProjectStore
from apps.control_plane.domain.project import PROVISIONAL_NAME, Project, ProjectStatus
from apps.control_plane.ports.events import Event, EventKind
from apps.control_plane.ports.store import MAX_HISTORY_PER_PROJECT, ProjectStore

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
"""Database 15 by default · not 0, so a stray run cannot touch a live demo."""


async def _reachable(url: str) -> bool:
    client = redis.from_url(url, socket_connect_timeout=0.5)
    try:
        await client.ping()
        return True
    except Exception:
        return False
    finally:
        await client.aclose()


@pytest.fixture(params=["memory", "redis"])
async def store(request):  # noqa: ANN001, ANN201 · pytest fixture
    if request.param == "memory":
        yield InMemoryProjectStore()
        return

    if not await _reachable(REDIS_URL):
        pytest.skip(f"no Redis at {REDIS_URL}")

    prefix = f"crew-test:{uuid.uuid4().hex[:8]}"
    adapter = RedisProjectStore(REDIS_URL, prefix=prefix)
    try:
        yield adapter
    finally:
        # Delete only what this test made · the fixture never flushes a db it
        # does not own.
        client = redis.from_url(REDIS_URL, decode_responses=True)
        keys = [key async for key in client.scan_iter(f"{prefix}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
        await adapter.aclose()


def project(project_id: str = "proj-1") -> Project:
    return Project(id=project_id)


def event(project_id: str = "proj-1", text: str = "hello") -> Event:
    return Event(
        project_id=project_id,
        kind=EventKind.FOUNDER_MESSAGE,
        source="founder",
        payload={"text": text},
    )


# ─── the contract ────────────────────────────────────────────────────────


async def test_satisfies_the_port(store: ProjectStore) -> None:
    assert isinstance(store, ProjectStore)


async def test_a_created_project_comes_back_whole(store: ProjectStore) -> None:
    """Every field, not just the id · a status lost in transit is a project
    the cap check counts forever."""
    created = await store.create(project())

    found = await store.get("proj-1")

    assert found == created
    assert found is not None
    assert found.name == PROVISIONAL_NAME
    assert found.status is ProjectStatus.SCOPING
    assert found.created_at == created.created_at
    assert found.created_at.tzinfo is not None
    assert found.renamed_at is None


async def test_unknown_project_is_none_not_an_error(store: ProjectStore) -> None:
    assert await store.get("proj-nope") is None


async def test_create_refuses_to_overwrite(store: ProjectStore) -> None:
    await store.create(project())
    with pytest.raises(ValueError):
        await store.create(project())


async def test_save_persists_a_rename(store: ProjectStore) -> None:
    created = await store.create(project())

    await store.save(created.rename("Checkout rewrite"))

    found = await store.get("proj-1")
    assert found is not None
    assert found.name == "Checkout rewrite"
    assert found.is_named
    assert found.renamed_at is not None


async def test_save_rejects_a_project_the_store_never_had(store: ProjectStore) -> None:
    with pytest.raises(KeyError):
        await store.save(project("proj-ghost"))


async def test_active_is_newest_first(store: ProjectStore) -> None:
    for index in range(3):
        await store.create(project(f"proj-{index}"))

    active = await store.list_active()

    assert [p.id for p in active] == ["proj-2", "proj-1", "proj-0"]


async def test_finished_projects_leave_the_active_list(store: ProjectStore) -> None:
    """The concurrency cap counts this list · a finished project that stays in
    it costs the founder a slot they are entitled to."""
    first = await store.create(project("proj-0"))
    await store.create(project("proj-1"))

    await store.save(first.finish())

    assert [p.id for p in await store.list_active()] == ["proj-1"]


async def test_cancelled_projects_leave_the_active_list(store: ProjectStore) -> None:
    first = await store.create(project("proj-0"))

    await store.save(first.finish(cancelled=True))

    assert await store.list_active() == []


async def test_history_is_oldest_first(store: ProjectStore) -> None:
    for index in range(3):
        await store.append_event(event(text=f"msg-{index}"))

    history = await store.history("proj-1")

    assert [e.payload["text"] for e in history] == ["msg-0", "msg-1", "msg-2"]


async def test_history_round_trips_the_whole_envelope(store: ProjectStore) -> None:
    original = Event(
        project_id="proj-1",
        kind=EventKind.TOOL_CALL,
        source="backend/crew-agent-backend-9f2a",
        payload={"tool": "write_file", "args": {"path": "app.py"}, "ok": True},
        at=1_766_000_000.5,
        to="reviewer/crew-agent-reviewer-1b3c",
    )

    await store.append_event(original)

    assert (await store.history("proj-1"))[0] == original


async def test_history_of_an_unknown_project_is_empty(store: ProjectStore) -> None:
    assert await store.history("proj-nope") == []


async def test_history_keeps_the_tail_within_the_limit(store: ProjectStore) -> None:
    over = 5
    for index in range(MAX_HISTORY_PER_PROJECT + over):
        await store.append_event(event(text=f"msg-{index}"))

    everything = await store.history("proj-1", limit=MAX_HISTORY_PER_PROJECT + over)

    assert len(everything) == MAX_HISTORY_PER_PROJECT
    assert everything[0].payload["text"] == f"msg-{over}"  # the oldest fell off


async def test_history_limit_returns_the_most_recent(store: ProjectStore) -> None:
    for index in range(10):
        await store.append_event(event(text=f"msg-{index}"))

    tail = await store.history("proj-1", limit=3)

    assert [e.payload["text"] for e in tail] == ["msg-7", "msg-8", "msg-9"]


async def test_projects_do_not_see_each_others_history(store: ProjectStore) -> None:
    await store.append_event(event("proj-a", "for a"))
    await store.append_event(event("proj-b", "for b"))

    assert [e.payload["text"] for e in await store.history("proj-a")] == ["for a"]
    assert [e.payload["text"] for e in await store.history("proj-b")] == ["for b"]


# ─── the part only Redis can be asked ────────────────────────────────────


async def test_state_survives_the_process_that_wrote_it() -> None:
    """The whole reason the store moved out of memory.

    A second adapter instance is what a restarted control plane is: same
    Redis, no shared objects, nothing carried over in the process.
    """
    if not await _reachable(REDIS_URL):
        pytest.skip(f"no Redis at {REDIS_URL}")

    prefix = f"crew-test:{uuid.uuid4().hex[:8]}"
    before = RedisProjectStore(REDIS_URL, prefix=prefix)
    try:
        created = await before.create(project())
        await before.save(created.rename("Checkout rewrite"))
        await before.append_event(event(text="ship it"))
    finally:
        await before.aclose()

    after = RedisProjectStore(REDIS_URL, prefix=prefix)
    try:
        found = await after.get("proj-1")
        assert found is not None
        assert found.name == "Checkout rewrite"
        assert [p.id for p in await after.list_active()] == ["proj-1"]
        assert [e.payload["text"] for e in await after.history("proj-1")] == ["ship it"]
    finally:
        client = redis.from_url(REDIS_URL, decode_responses=True)
        keys = [key async for key in client.scan_iter(f"{prefix}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
        await after.aclose()
