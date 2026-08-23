"""The event wire format · shared by the control plane and every agent.

This lives outside both because it belongs to neither. When the control plane
owned it, the agents drifted: agents published ``{"from", "type", ...}`` onto
``crew/task/<id>/messages`` while the control plane read ``{"source", "kind"}``
from ``crew/project/<id>/events``, and nothing failed loudly — the events
simply never arrived.

One definition, imported by both sides, so a change to the envelope cannot
land on one side only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventKind(StrEnum):
    """The kinds of thing that happen. A closed set on purpose.

    A free-form string means the UI has to guess, and a typo becomes an event
    nobody renders.
    """

    FOUNDER_MESSAGE = "founder_message"
    """The founder said something into the project."""

    AGENT_MESSAGE = "agent_message"
    """An agent's final reply for a turn."""

    AGENT_THINKING = "agent_thinking"
    """Intermediate reasoning · shown live, not part of the transcript."""

    AGENT_SPAWNED = "agent_spawned"
    AGENT_READY = "agent_ready"
    """An agent has booted and announced its tools."""

    AGENT_FINISHED = "agent_finished"

    TOOL_CALL = "tool_call"
    """An agent used a tool · this is what makes the harness visible."""

    ASSIGNMENT = "assignment"
    """Work handed from one agent to another."""

    ESCALATION = "escalation"
    """A decision has been handed to the founder."""

    PROJECT_RENAMED = "project_renamed"
    """The placeholder name was replaced with a real one."""

    SPAWN_REFUSED = "spawn_refused"
    """A cap said no. Surfaced so the refusal is not silent."""

    USAGE = "usage"
    """Token counts for one turn."""

    ERROR = "error"


ACTIONABLE_KINDS = frozenset(
    {
        EventKind.FOUNDER_MESSAGE,
        EventKind.ASSIGNMENT,
        EventKind.AGENT_MESSAGE,
    }
)
"""The kinds that make an agent *work*.

Everything else is telemetry for the console. Reacting to telemetry caused a
broadcast storm once — every agent re-invoked on every other agent's output
until the Gemini quota was gone. Keep this set small.
"""


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, addressed to a project."""

    project_id: str
    kind: EventKind
    source: str
    """Who emitted it — ``"founder"``, ``"control-plane"``, or ``role/name``."""

    payload: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)
    to: str = ""
    """Optional addressee. Empty means everyone on the project.

    Directed delivery is what keeps a five-agent project from turning every
    message into five more.
    """

    def is_for(self, agent: str) -> bool:
        """Should this agent act on this event?"""
        if self.source == agent:
            return False  # never react to your own output
        return not self.to or self.to == agent


def channel_for(project_id: str) -> str:
    """The pub/sub channel carrying one project's events.

    One channel per project, so three concurrent projects do not deliver each
    other's traffic to three browser tabs.
    """
    return f"crew/project/{project_id}/events"


ALL_PROJECTS_PATTERN = "crew/project/*/events"
"""Matches every project's channel · used by the control plane's recorder."""
