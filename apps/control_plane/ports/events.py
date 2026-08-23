"""The EventBus port · how the crew's activity reaches anyone watching.

The envelope itself lives in :mod:`contracts.events`, shared with the agents.
This file holds only the contract for *delivering* one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from contracts.events import Event, EventKind

__all__ = ["Event", "EventBus", "EventKind"]


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

    def subscribe_all(self) -> AsyncIterator[Event]:
        """Yield events from every project.

        Used by the recorder, which is what makes replay possible at all: the
        agents publish onto the bus from inside their own pods, so without one
        subscriber writing everything down, a project's history would contain
        only what the control plane itself said.
        """
        ...
