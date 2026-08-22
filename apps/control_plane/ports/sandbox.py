"""The SandboxBackend port · where agent-written code is allowed to run.

An agent that writes code eventually wants to run it, and running model-written
code in the control plane's own process is how a demo becomes an incident. So
execution goes somewhere disposable, and this contract is what "somewhere
disposable" has to provide.

Two adapters are planned:

* ``k8s_sandbox`` — a short-lived Job in the cluster, no network, read-only
  root filesystem, the project's workspace mounted in.
* ``e2b_sandbox`` — E2B's hosted microVM, when isolation from *our own* cluster
  matters more than keeping everything local.

Neither is written yet; the port exists so the choice stays a one-line swap
instead of a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExecResult:
    """What came back from running something."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """A reference to a live sandbox."""

    id: str
    project_id: str


@runtime_checkable
class SandboxBackend(Protocol):
    """Somewhere untrusted code can run and then be thrown away."""

    async def create(self, project_id: str) -> SandboxHandle:
        """Bring up a sandbox for a project's workspace."""
        ...

    async def exec(
        self, handle: SandboxHandle, command: list[str], *, timeout_s: int = 120
    ) -> ExecResult:
        """Run a command inside the sandbox.

        ``command`` is a list, never a string · nothing is handed to a shell,
        so a filename an agent invented cannot become an argument.
        """
        ...

    async def destroy(self, handle: SandboxHandle) -> None: ...
