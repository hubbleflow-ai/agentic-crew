"""Request and response shapes · the HTTP edge, and nothing more.

These types exist so FastAPI can validate and document the API. They are
deliberately separate from the domain types: an HTTP field renamed for the
frontend's convenience should never force a change to a rule.
"""

from __future__ import annotations

from apps.control_plane.domain.caps import AgentRole
from apps.control_plane.domain.project import MAX_NAME_LENGTH, Project
from apps.control_plane.ports.events import Event
from pydantic import BaseModel, Field


class OpenProjectRequest(BaseModel):
    request: str = Field(min_length=1, description="What the founder asked for.")


class RenameProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class SpawnRequest(BaseModel):
    role: AgentRole
    assignment: str = ""
    override: bool = Field(
        default=False,
        description=(
            "Founder approval to exceed the per-project cap. "
            "Never lifts the cluster-wide one."
        ),
    )


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)


class DelegateRequest(BaseModel):
    sender: str
    to_role: AgentRole
    text: str = Field(min_length=1)


class EscalationRequest(BaseModel):
    question: str = Field(min_length=1)
    context: str = ""
    options: list[str] = Field(default_factory=list)


class ProjectView(BaseModel):
    id: str
    name: str
    status: str
    is_named: bool
    created_at: str

    @classmethod
    def of(cls, project: Project) -> ProjectView:
        return cls(
            id=project.id,
            name=project.name,
            status=project.status.value,
            is_named=project.is_named,
            created_at=project.created_at.isoformat(),
        )


class EventView(BaseModel):
    kind: str
    source: str
    payload: dict
    at: float

    @classmethod
    def of(cls, event: Event) -> EventView:
        return cls(kind=event.kind.value, source=event.source, payload=event.payload, at=event.at)


class AgentView(BaseModel):
    name: str
    role: str
    state: str
    reason: str = ""
    stuck: bool = False
