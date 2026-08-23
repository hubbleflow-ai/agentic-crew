"""The HTTP surface, driven against fake adapters · no cluster, no Redis."""

import pytest
from apps.control_plane.adapters.fake_runtime import FakeAgentRuntime
from apps.control_plane.adapters.memory_store import InMemoryProjectStore
from apps.control_plane.api.routes import router
from apps.control_plane.domain.project import PROVISIONAL_NAME
from apps.control_plane.ports.events import Event
from apps.control_plane.service import CrewService
from fastapi import FastAPI
from fastapi.testclient import TestClient


class RecordingBus:
    """Bus plus recorder · see tests/test_service.py for why they are one here."""

    def __init__(self, store: InMemoryProjectStore) -> None:
        self._store = store

    async def publish(self, event: Event) -> None:
        await self._store.append_event(event)

    def subscribe(self, project_id: str):  # noqa: ANN201
        raise NotImplementedError


@pytest.fixture
def client() -> TestClient:
    """The real app minus its lifespan · this is what the ports bought us."""
    app = FastAPI()
    app.include_router(router)
    store = InMemoryProjectStore()
    app.state.service = CrewService(
        store=store, runtime=FakeAgentRuntime(), events=RecordingBus(store)
    )
    return TestClient(app)


def open_project(client: TestClient, text: str = "Add a /healthz endpoint") -> dict:
    response = client.post("/projects", json={"request": text})
    assert response.status_code == 201, response.text
    return response.json()


class TestProjects:
    def test_opens_with_the_placeholder_name(self, client: TestClient) -> None:
        body = open_project(client)
        assert body["name"] == PROVISIONAL_NAME
        assert body["is_named"] is False
        assert body["status"] == "running"

    def test_rename_flips_is_named(self, client: TestClient) -> None:
        project = open_project(client)
        body = client.patch(
            f"/projects/{project['id']}", json={"name": "Healthz endpoint"}
        ).json()
        assert (body["name"], body["is_named"]) == ("Healthz endpoint", True)

    def test_rejects_the_placeholder_as_a_name(self, client: TestClient) -> None:
        project = open_project(client)
        response = client.patch(f"/projects/{project['id']}", json={"name": "New Project"})
        assert response.status_code == 422

    def test_unknown_project_is_404_not_500(self, client: TestClient) -> None:
        assert client.get("/projects/proj-nope").status_code == 404
        spawn = client.post("/projects/proj-nope/agents", json={"role": "qa_engineer"})
        assert spawn.status_code == 404

    def test_concurrent_projects_are_listed(self, client: TestClient) -> None:
        ids = {open_project(client, f"job {i}")["id"] for i in range(3)}
        assert {p["id"] for p in client.get("/projects").json()} == ids

    def test_a_fourth_project_is_refused_with_the_reason(self, client: TestClient) -> None:
        for i in range(3):
            open_project(client, f"job {i}")
        response = client.post("/projects", json={"request": "one more"})
        assert response.status_code == 409
        assert response.json()["detail"]["scope"] == "projects"


class TestAgents:
    def test_a_new_project_already_has_its_em(self, client: TestClient) -> None:
        project = open_project(client)
        roster = client.get(f"/projects/{project['id']}/agents").json()
        assert [a["role"] for a in roster] == ["engineering_manager"]
        assert roster[0]["state"] == "running"

    def test_spawning_past_the_cap_returns_409_with_guidance(self, client: TestClient) -> None:
        project = open_project(client)
        for _ in range(4):
            assert client.post(
                f"/projects/{project['id']}/agents", json={"role": "backend_engineer"}
            ).status_code == 201

        refused = client.post(
            f"/projects/{project['id']}/agents", json={"role": "backend_engineer"}
        )
        assert refused.status_code == 409
        detail = refused.json()["detail"]
        assert detail["scope"] == "project"
        assert "Do not retry" in detail["message"]

    def test_override_is_accepted_for_the_project_cap(self, client: TestClient) -> None:
        project = open_project(client)
        for _ in range(4):
            client.post(f"/projects/{project['id']}/agents", json={"role": "backend_engineer"})
        assert client.post(
            f"/projects/{project['id']}/agents",
            json={"role": "backend_engineer", "override": True},
        ).status_code == 201

    def test_an_unknown_role_is_rejected_by_validation(self, client: TestClient) -> None:
        project = open_project(client)
        assert client.post(
            f"/projects/{project['id']}/agents", json={"role": "chief_vibes_officer"}
        ).status_code == 422


class TestHistory:
    def test_replays_the_opening_exchange(self, client: TestClient) -> None:
        project = open_project(client, "Add a /healthz endpoint")
        events = client.get(f"/projects/{project['id']}/events").json()
        assert [e["kind"] for e in events] == ["founder_message", "agent_spawned"]
        assert events[0]["payload"]["text"] == "Add a /healthz endpoint"

    def test_a_founder_follow_up_is_recorded(self, client: TestClient) -> None:
        project = open_project(client)
        assert client.post(
            f"/projects/{project['id']}/messages", json={"text": "make it async"}
        ).status_code == 202
        kinds = [e["kind"] for e in client.get(f"/projects/{project['id']}/events").json()]
        assert kinds[-1] == "founder_message"

    def test_a_refusal_appears_in_history(self, client: TestClient) -> None:
        project = open_project(client)
        client.post(f"/projects/{project['id']}/agents", json={"role": "engineering_manager"})
        kinds = [e["kind"] for e in client.get(f"/projects/{project['id']}/events").json()]
        assert "spawn_refused" in kinds
