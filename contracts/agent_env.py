"""The environment contract · what a Job sets, and what an agent reads.

These names were previously written in two places that disagreed: the launcher
set ``AGENT_ROLE``/``PROJECT_ID`` and the agent read ``CREW_ROLE``/
``CREW_TASK_ID``. Nothing detected it until a container started and crashed on
a ``KeyError``.

Now both sides import these constants, so a rename that misses one side does
not compile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ROLE = "AGENT_ROLE"
PROJECT_ID = "PROJECT_ID"
AGENT_NAME = "AGENT_NAME"
ASSIGNMENT = "ASSIGNMENT"

REDIS_URL = "REDIS_URL"
CONTROL_PLANE_URL = "CONTROL_PLANE_URL"
GEMINI_MODEL = "GEMINI_MODEL"

SKILLS_ROOT = "/app/skills"
"""Where the skills tree is baked into the image.

In the image rather than on the volume on purpose: skills are code, they are
reviewed and versioned with the repo, and an agent that could write to its own
instructions is an agent that can talk itself into anything.
"""

WORKSPACE = "/workspace"
"""Where the project's shared volume is mounted.

Not ``/workspace/<project-id>`` · the Job mounts the volume with
``subPath: <project-id>``, so the agent sees only its own project's files and
cannot walk up into another's.
"""


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Who this container is, read once at boot."""

    role: str
    project_id: str
    name: str
    assignment: str
    redis_url: str
    control_plane_url: str
    model: str

    @classmethod
    def from_env(cls) -> AgentIdentity:
        """Read the identity the Job injected.

        Missing required values raise here, at boot, rather than at the first
        message · a container that cannot know who it is should not start.
        """
        return cls(
            role=os.environ[ROLE],
            project_id=os.environ[PROJECT_ID],
            name=os.environ.get(AGENT_NAME) or os.environ[ROLE],
            assignment=os.environ.get(ASSIGNMENT, ""),
            redis_url=os.environ.get(REDIS_URL, "redis://redis:6379/0"),
            control_plane_url=os.environ.get(CONTROL_PLANE_URL, "http://control-plane:8000"),
            model=os.environ.get(GEMINI_MODEL, "gemini-3.5-flash"),
        )

    @property
    def address(self) -> str:
        """How this agent appears as an event's ``source``."""
        return f"{self.role}/{self.name}"
