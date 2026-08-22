"""Spawn caps · how many agents of a role may exist at once.

Two ceilings, and they answer different questions:

* **per-project** — a quality limit. Four backend engineers on one project is
  a crowd; the fifth is a sign the work was not decomposed.
* **global** — a resource limit. Three projects each allowed four backend
  engineers is twelve pods, and the cluster has to fit them.

The old implementation had only the per-project ceiling and kept the tally in a
dictionary inside the control-plane process, so a restart forgot every running
agent and the cap silently reset. Here the tally is passed in — the caller
counts live Jobs by label, which is the cluster's own view and survives
anything.

No I/O in this module. Given a census, it answers yes or no.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AgentRole(StrEnum):
    """The specialists a crew can contain.

    Only roles listed here can be spawned. A role with no implementation
    behind it is not a role — it is a promise, and promises do not run.
    """

    ENGINEERING_MANAGER = "engineering_manager"
    PRODUCT_MANAGER = "product_manager"
    BACKEND_ENGINEER = "backend_engineer"
    FRONTEND_ENGINEER = "frontend_engineer"
    QA_ENGINEER = "qa_engineer"
    CODE_REVIEWER = "code_reviewer"


PER_PROJECT_LIMITS: dict[AgentRole, int] = {
    AgentRole.ENGINEERING_MANAGER: 1,
    AgentRole.PRODUCT_MANAGER: 1,
    AgentRole.BACKEND_ENGINEER: 4,
    AgentRole.FRONTEND_ENGINEER: 2,
    AgentRole.QA_ENGINEER: 2,
    AgentRole.CODE_REVIEWER: 2,
}

GLOBAL_LIMITS: dict[AgentRole, int] = {
    AgentRole.ENGINEERING_MANAGER: 6,
    AgentRole.PRODUCT_MANAGER: 6,
    AgentRole.BACKEND_ENGINEER: 12,
    AgentRole.FRONTEND_ENGINEER: 6,
    AgentRole.QA_ENGINEER: 6,
    AgentRole.CODE_REVIEWER: 6,
}

MAX_CONCURRENT_PROJECTS = 3


@dataclass(frozen=True, slots=True)
class Census:
    """How many agents of one role are alive, at both scopes.

    Built by counting live Jobs by label selector — never from memory.
    """

    role: AgentRole
    in_project: int
    everywhere: int


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a spawn was refused, in words an agent can act on.

    The message is read by a model, so it says what to do instead of merely
    reporting a number. A bare "cap reached" invites a retry loop.
    """

    scope: str
    limit: int
    current: int
    message: str


def check_spawn(census: Census, *, override: bool = False) -> Refusal | None:
    """Decide whether one more agent of this role may start.

    Returns ``None`` when the spawn is allowed, or a :class:`Refusal`
    explaining which ceiling was hit.

    ``override`` bypasses the per-project limit only. The global limit is a
    resource ceiling and is never overridable — no amount of founder approval
    creates more cluster.
    """
    global_limit = GLOBAL_LIMITS[census.role]
    if census.everywhere >= global_limit:
        return Refusal(
            scope="global",
            limit=global_limit,
            current=census.everywhere,
            message=(
                f"{census.everywhere} {census.role} agents are already running across "
                f"all projects, which is the cluster-wide limit of {global_limit}. "
                "This cannot be overridden. Wait for one to finish."
            ),
        )

    project_limit = PER_PROJECT_LIMITS[census.role]
    if census.in_project >= project_limit and not override:
        return Refusal(
            scope="project",
            limit=project_limit,
            current=census.in_project,
            message=(
                f"This project already has {census.in_project} of {project_limit} "
                f"{census.role} agents. Give the work to one of them, or split it "
                "differently. Do not retry this spawn — ask the founder to approve "
                "an override if you genuinely need another."
            ),
        )

    return None


def check_new_project(active_projects: int) -> Refusal | None:
    """Decide whether another project may be opened."""
    if active_projects >= MAX_CONCURRENT_PROJECTS:
        return Refusal(
            scope="projects",
            limit=MAX_CONCURRENT_PROJECTS,
            current=active_projects,
            message=(
                f"{active_projects} projects are already running, which is the "
                f"limit of {MAX_CONCURRENT_PROJECTS}. Finish or cancel one first."
            ),
        )
    return None
