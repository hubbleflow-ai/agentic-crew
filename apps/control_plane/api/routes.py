"""HTTP routes · translate, delegate, translate back.

Every handler does the same three things: turn the request into domain terms,
call one use case, and turn the result into JSON. No rule is decided here — if
a handler starts branching on how many agents exist, that logic belongs in
:mod:`apps.control_plane.service` or :mod:`apps.control_plane.domain`.
"""

from __future__ import annotations

import json
from typing import Annotated

from apps.control_plane.api.deps import get_service
from apps.control_plane.api.schemas import (
    EscalationRequest,
    EventView,
    MessageRequest,
    OpenProjectRequest,
    ProjectView,
    RenameProjectRequest,
    SpawnRequest,
)
from apps.control_plane.domain.project import Project
from apps.control_plane.ports.events import Event
from apps.control_plane.service import CapExceeded, CrewService
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

router = APIRouter()

Service = Annotated[CrewService, Depends(get_service)]


@router.post("/projects", response_model=ProjectView, status_code=201)
async def open_project(body: OpenProjectRequest, service: Service) -> ProjectView:
    """Start a project. It gets an EM and a placeholder name."""
    try:
        return ProjectView.of(await service.open_project(body.request))
    except CapExceeded as exc:
        raise _refused(exc) from exc


@router.get("/projects", response_model=list[ProjectView])
async def list_projects(service: Service) -> list[ProjectView]:
    return [ProjectView.of(p) for p in await service.list_active()]


@router.get("/projects/{project_id}", response_model=ProjectView)
async def get_project(project_id: str, service: Service) -> ProjectView:
    return ProjectView.of(await _lookup(service, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectView)
async def rename_project(
    project_id: str, body: RenameProjectRequest, service: Service
) -> ProjectView:
    """Replace the placeholder name · called by the EM once scope is clear."""
    try:
        return ProjectView.of(await service.rename_project(project_id, body.name))
    except KeyError as exc:
        raise HTTPException(404, f"no project {project_id}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/projects/{project_id}", response_model=ProjectView)
async def close_project(project_id: str, service: Service) -> ProjectView:
    try:
        return ProjectView.of(await service.finish_project(project_id, cancelled=True))
    except KeyError as exc:
        raise HTTPException(404, f"no project {project_id}") from exc


@router.post("/projects/{project_id}/agents", status_code=201)
async def spawn_agent(project_id: str, body: SpawnRequest, service: Service) -> dict:
    """Add a specialist. Refused with a 409 when a cap says no."""
    try:
        result = await service.spawn(
            project_id, body.role, assignment=body.assignment, override=body.override
        )
    except KeyError as exc:
        raise HTTPException(404, f"no project {project_id}") from exc
    except CapExceeded as exc:
        raise _refused(exc) from exc
    return {"agent": result.handle.name, "role": result.handle.role.value}


@router.get("/projects/{project_id}/agents")
async def list_agents(project_id: str, service: Service) -> list[dict]:
    await _lookup(service, project_id)
    return await service.roster(project_id)


@router.post("/projects/{project_id}/messages", status_code=202)
async def post_message(project_id: str, body: MessageRequest, service: Service) -> dict:
    """A founder reply · reaches every agent watching the project."""
    await _lookup(service, project_id)
    await service.founder_says(project_id, body.text)
    return {"accepted": True}


@router.post("/projects/{project_id}/escalations", status_code=202)
async def escalate(project_id: str, body: EscalationRequest, service: Service) -> dict:
    """An agent hands a decision to the founder.

    Acknowledged immediately and deliberately non-blocking · a headless run
    with nobody watching must not deadlock a whole crew on a question that
    will never be answered. The founder's reply, if it comes, arrives later as
    an ordinary message on the project.
    """
    await _lookup(service, project_id)
    await service.escalate(
        project_id, question=body.question, context=body.context, options=body.options
    )
    return {
        "acknowledged": True,
        "guidance": (
            "Raised with the founder. If no answer arrives, proceed on your best "
            "judgement and record the assumption in the ticket."
        ),
    }


@router.get("/projects/{project_id}/events", response_model=list[EventView])
async def project_history(project_id: str, service: Service, limit: int = 200) -> list[EventView]:
    """What already happened · the live feed is the WebSocket below."""
    await _lookup(service, project_id)
    return [EventView.of(e) for e in await service.history(project_id, limit=limit)]


@router.websocket("/projects/{project_id}/stream")
async def stream(ws: WebSocket, project_id: str) -> None:
    """Live events for one project.

    History first, then the live feed · a tab that opens mid-project shows the
    conversation so far instead of an empty pane.
    """
    service: CrewService = ws.app.state.service
    await ws.accept()
    try:
        for past in await service.history(project_id):
            await ws.send_text(_encode(past))
        async for event in service.watch(project_id):
            await ws.send_text(_encode(event))
    except WebSocketDisconnect:
        pass


def _encode(event: Event) -> str:
    return json.dumps(EventView.of(event).model_dump())


async def _lookup(service: CrewService, project_id: str) -> Project:
    try:
        return await service.get_project(project_id)
    except KeyError as exc:
        raise HTTPException(404, f"no project {project_id}") from exc


def _refused(exc: CapExceeded) -> HTTPException:
    """409, with the refusal intact.

    The body carries the message verbatim because an agent reads it and needs
    to know what to do instead · a bare status code invites a retry loop.
    """
    r = exc.refusal
    return HTTPException(
        status_code=409,
        detail={"scope": r.scope, "limit": r.limit, "current": r.current, "message": r.message},
    )

