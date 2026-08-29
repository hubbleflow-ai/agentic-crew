"""The AgentRuntime port · what the crew needs from whatever runs its agents.

This file says **what** is required, never **how**. Kubernetes satisfies it by
creating a Job; Docker satisfies it by running a container; a test satisfies it
with a dictionary. None of them appear here.

The whole point is that the rules in ``domain/`` and the routes in ``api/``
depend on this contract and nothing else, so swapping the cluster for Docker —
or for a fake, in a test — never touches a business rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from apps.control_plane.domain.caps import AgentRole


class AgentState(StrEnum):
    """Where a launched agent has got to.

    Deliberately coarse. The control plane cares whether an agent is coming up,
    working, or finished — not which of Kubernetes' many pod phases it is in.
    """

    PENDING = "pending"
    """Accepted by the runtime, not yet executing."""

    RUNNING = "running"
    """Actually executing · the container is up."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (AgentState.SUCCEEDED, AgentState.FAILED)


@dataclass(frozen=True, slots=True)
class AgentStatus:
    """Where an agent is, plus why — when "why" is the interesting part.

    A bare state hides the failure that costs the most time: a container that
    never starts because a Secret is missing sits there looking busy. The
    runtime knows the reason; this carries it out instead of discarding it.
    """

    state: AgentState
    reason: str = ""
    """The runtime's own word for the situation, e.g. ``ImagePullBackOff``."""

    detail: str = ""

    @property
    def is_stuck(self) -> bool:
        """Not progressing, and no amount of waiting will change that.

        Kubernetes retries a bad image or a missing Secret forever without ever
        counting a failure, so nothing times these out on our behalf.
        """
        return self.state is AgentState.PENDING and self.reason in _STUCK_REASONS


_STUCK_REASONS = frozenset(
    {
        "CreateContainerConfigError",  # a referenced Secret or ConfigMap is absent
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
        "CreateContainerError",
    }
)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """A request for one agent. Everything the runtime needs to start it."""

    role: AgentRole
    project_id: str
    assignment: str = ""
    """The brief handed to the agent when it boots."""


@dataclass(frozen=True, slots=True)
class AgentHandle:
    """A reference to a launched agent, returned by the runtime.

    ``name`` is whatever the runtime calls it — a Job name under Kubernetes, a
    container name under Docker. Callers treat it as opaque.
    """

    id: str
    name: str
    role: AgentRole
    project_id: str


@runtime_checkable
class AgentRuntime(Protocol):
    """Somewhere agents can be run.

    Implementations live in ``adapters/``:

    * ``k8s_runtime`` — creates a Job and lets the cluster own the lifecycle:
      placement, retry, completion, and TTL cleanup. This is the real one.
    * ``fake_runtime`` — an in-memory stand-in so the spawn rules can be tested
      without a cluster.

    There is deliberately no Docker implementation. Running containers by hand
    means re-implementing retry, cleanup and placement in application code,
    which is the thing this project moved away from.
    """

    async def launch(self, spec: AgentSpec) -> AgentHandle:
        """Ask for an agent. Returns as soon as the request is accepted.

        This does not wait for the agent to be ready. Under Kubernetes it is
        one API call and then the cluster's problem.
        """
        ...

    async def status(self, handle: AgentHandle) -> AgentStatus:
        """Where the agent has got to, and why if it is not moving."""
        ...

    async def census(self, role: AgentRole, project_id: str | None = None) -> int:
        """Count live agents of a role, optionally within one project.

        Counted from the runtime's own view — a label selector over Jobs, not a
        tally held in this process. That is what makes the spawn caps survive a
        control-plane restart.
        """
        ...

    async def handles(self, role: AgentRole, project_id: str) -> list[AgentHandle]:
        """The live agents of a role on a project, not just how many.

        ``census`` answers "may I spawn another"; this answers "who is on this
        project" · the roster the founder sees.
        """
        ...

    async def stop_project(self, project_id: str) -> int:
        """Kill every agent on a project. Returns how many were stopped.

        The control plane could create agents and never end them, which is how
        one run spent 70M tokens: a reviewer's comment is an event, the
        engineer reacts, that reply is another event, and nothing in the loop
        ever says finished. Creation without deletion is not a lifecycle.
        """
        ...

    async def logs(self, handle: AgentHandle, *, tail: int = 200) -> str: ...
