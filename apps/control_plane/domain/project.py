"""Projects · the top-level unit of work.

A founder's conversation becomes a project. It does not get a real name
straight away: the opening exchange is often a greeting or a vague question,
and naming a project "hi" helps nobody. So a project starts as
``PROVISIONAL_NAME`` and is renamed once the Engineering Manager has scoped
something concrete.

Nothing in this module performs I/O. Every rule here can be exercised in a unit
test with no cluster, no database and no Docker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum

PROVISIONAL_NAME = "New Project"
"""What a project is called before anyone knows what it is."""

MAX_NAME_LENGTH = 60

_WHITESPACE = re.compile(r"\s+")


class ProjectStatus(StrEnum):
    """Where a project is in its life."""

    SCOPING = "scoping"
    """Opened. The EM is triaging, or the PM is still clarifying."""

    RUNNING = "running"
    """Scope is settled and specialists are working."""

    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (ProjectStatus.DONE, ProjectStatus.CANCELLED)


class NamingError(ValueError):
    """A proposed project name was rejected."""


@dataclass(frozen=True, slots=True)
class Project:
    """One founder conversation and the work that comes out of it.

    Frozen on purpose. Transitions return a new instance rather than mutating,
    so a caller can never half-apply a change and leave the project in a state
    the rules forbid.
    """

    id: str
    name: str = PROVISIONAL_NAME
    status: ProjectStatus = ProjectStatus.SCOPING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    renamed_at: datetime | None = None

    @property
    def is_named(self) -> bool:
        """True once the project has a real name rather than the placeholder.

        The UI uses this to decide whether to show the chat title as settled.
        """
        return self.renamed_at is not None

    def rename(self, proposed: str, *, now: datetime | None = None) -> Project:
        """Give the project its real name.

        Called by the EM once a concrete request has been scoped — never
        during small talk.

        Raises:
            NamingError: the name is empty, too long, or the placeholder.
        """
        name = _WHITESPACE.sub(" ", proposed).strip()

        if not name:
            raise NamingError("a project name cannot be empty")
        if len(name) > MAX_NAME_LENGTH:
            raise NamingError(
                f"project name is {len(name)} characters; the limit is {MAX_NAME_LENGTH}"
            )
        if name.casefold() == PROVISIONAL_NAME.casefold():
            raise NamingError(
                f"{PROVISIONAL_NAME!r} is the placeholder, not a name. "
                "Name the project after what is being built."
            )

        return replace(self, name=name, renamed_at=now or datetime.now(UTC))

    def start(self) -> Project:
        """Move from scoping to running, once specialists are at work."""
        if self.status.is_terminal:
            raise ValueError(f"project {self.id} is already {self.status}")
        return replace(self, status=ProjectStatus.RUNNING)

    def finish(self, *, cancelled: bool = False) -> Project:
        """Close the project."""
        return replace(
            self,
            status=ProjectStatus.CANCELLED if cancelled else ProjectStatus.DONE,
        )
