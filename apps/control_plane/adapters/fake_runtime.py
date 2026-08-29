"""In-memory runtime · the cluster, replaced by a dictionary.

Its job is to make the *rules* testable. Whether the founder can spawn a fifth
backend engineer is a policy question, and answering it should not require
Docker, minikube, or a network. With this adapter the whole spawn path runs in
milliseconds inside a unit test.

It is also the honest way to demonstrate a port: two implementations, one
contract, and nothing above the port able to tell them apart.
"""

from __future__ import annotations

import itertools

from apps.control_plane.domain.caps import AgentRole
from apps.control_plane.ports.runtime import (
    AgentHandle,
    AgentSpec,
    AgentState,
    AgentStatus,
)


class FakeAgentRuntime:
    """Records launches and lets a test drive their states by hand.

    Satisfies :class:`~apps.control_plane.ports.runtime.AgentRuntime`.
    """

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self.launched: list[AgentSpec] = []
        self._states: dict[str, AgentStatus] = {}
        self._specs: dict[str, AgentSpec] = {}
        self._logs: dict[str, str] = {}

    async def launch(self, spec: AgentSpec) -> AgentHandle:
        name = f"{spec.role.value}-{next(self._counter)}"
        self.launched.append(spec)
        self._specs[name] = spec
        self._states[name] = AgentStatus(AgentState.RUNNING)
        return AgentHandle(id=name, name=name, role=spec.role, project_id=spec.project_id)

    async def stop_project(self, project_id: str) -> int:
        """Forget every agent on a project, as a cluster would delete them."""
        doomed = [n for n, spec in self._specs.items() if spec.project_id == project_id]
        for name in doomed:
            self._states[name] = AgentStatus(AgentState.SUCCEEDED)
            del self._specs[name]
        return len(doomed)

    async def status(self, handle: AgentHandle) -> AgentStatus:
        return self._states.get(handle.name, AgentStatus(AgentState.SUCCEEDED))

    async def census(self, role: AgentRole, project_id: str | None = None) -> int:
        """Count the same way the real adapter does · live agents only."""
        return len(self._live(role, project_id))

    def _live(self, role: AgentRole, project_id: str | None) -> list[str]:
        return [
            name
            for name, spec in self._specs.items()
            if spec.role is role
            and (project_id is None or spec.project_id == project_id)
            and not self._states[name].state.is_terminal
        ]

    async def handles(self, role: AgentRole, project_id: str) -> list[AgentHandle]:
        return [
            AgentHandle(id=name, name=name, role=role, project_id=project_id)
            for name in self._live(role, project_id)
        ]

    async def logs(self, handle: AgentHandle, *, tail: int = 200) -> str:
        return self._logs.get(handle.name, "")

    # ─── test controls · not part of the port ────────────────────────────

    def finish(self, handle: AgentHandle, *, failed: bool = False) -> None:
        """End an agent, so a test can prove its slot is released."""
        self._states[handle.name] = AgentStatus(
            AgentState.FAILED if failed else AgentState.SUCCEEDED
        )

    def emit(self, handle: AgentHandle, text: str) -> None:
        self._logs[handle.name] = text
