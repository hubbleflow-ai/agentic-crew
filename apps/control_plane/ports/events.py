"""The EventBus port · how the crew's activity reaches anyone watching.

Every visible thing an agent does — a message, a tool call, a spawn, an
escalation — becomes an :class:`Event` on a project's topic. The web UI, the
CLI, and any future subscriber all read the same stream.

The current adapter is Redis pub/sub. Nothing above this line knows that, so
the day the transport changes, only ``adapters/`` moves.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class EventKind(StrEnum):
    """The kinds of thing that happen. A closed set on purpose.

    A free-form string here means the UI has to guess, and a typo becomes an
    event nobody renders.
    """

    FOUNDER_MESSAGE = "founder_message"
    """The founder said something into the project."""

    AGENT_MESSAGE = "agent_message"
    """An agent said something back."""

    AGENT_SPAWNED = "agent_spawned"
    AGENT_FINISHED = "agent_finished"

    TOOL_CALL = "tool_call"
    """An agent used a tool · this is what makes the harness visible."""

    ESCALATION = "escalation"
    """A decision has been handed to the founder."""

    PROJECT_RENAMED = "project_renamed"
    """The placeholder name was replaced with a real one."""

    SPAWN_REFUSED = "spawn_refused"
    """A cap said no. Surfaced so the refusal is not silent."""


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, addressed to a project."""

    project_id: str
    kind: EventKind
    source: str
    """Who emitted it — a role name, ``"founder"``, or ``"control-plane"``."""

    payload: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)


@runtime_checkable
class EventBus(Protocol):
    """Fan-out of events to whoever is watching a project."""

    async def publish(self, event: Event) -> None: ...

    def subscribe(self, project_id: str) -> AsyncIterator[Event]:
        """Yield events for one project until the caller stops iterating.

        Live-only: a subscriber joining late sees what happens next, not what
        it missed. Replay belongs to the project store, not the bus.
        """
        ...
