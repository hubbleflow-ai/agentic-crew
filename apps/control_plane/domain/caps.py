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
    AgentRole.BACKEND_ENGINEER: 1,
    AgentRole.FRONTEND_ENGINEER: 1,
    # QA checks once and does not check again · the prompts enforce the round
    # count, this enforces the head count.
    #
    # Zero is "this crew does not use that role", enforced rather than asked
    # for. The code reviewer is what made a project unbounded: a review
    # comment is an event, the engineer reacts to it, that reply is another
    # event, and the reviewer reacts again. Each hop is a fresh turn, so the
    # per-turn model-call ceiling resets every time and nothing ever says
    # "finished". One run cost 70M tokens in twenty minutes that way.
    AgentRole.QA_ENGINEER: 1,
    AgentRole.CODE_REVIEWER: 0,
}

GLOBAL_LIMITS: dict[AgentRole, int] = {
    AgentRole.ENGINEERING_MANAGER: 3,
    AgentRole.PRODUCT_MANAGER: 3,
    AgentRole.BACKEND_ENGINEER: 3,
    AgentRole.FRONTEND_ENGINEER: 3,
    AgentRole.QA_ENGINEER: 1,
    AgentRole.CODE_REVIEWER: 0,
}

MAX_CONCURRENT_PROJECTS = 1


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
    # Asked first, and not overridable: a limit of zero is not crowding, it is
    # "this crew has no such role". Checked before the global ceiling so the
    # refusal says that, rather than "0 of a limit of 0".
    if PER_PROJECT_LIMITS[census.role] == 0:
        return Refusal(
            scope="role",
            limit=0,
            current=census.in_project,
            message=(
                f"This crew has no {census.role} role. Do not spawn one and do not "
                "retry. If the work needs checking, do it yourself before you finish."
            ),
        )

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
