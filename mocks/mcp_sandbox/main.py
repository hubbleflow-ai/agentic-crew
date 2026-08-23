"""The sandbox service · where agent-written code is allowed to run.

Files live on the project's shared volume, so what a backend engineer writes
is on disk for the reviewer to read. Execution happens somewhere else entirely:
each command becomes its own Kubernetes Job, with no cluster credentials, a
read-only root filesystem, a hard deadline, and the project's workspace as the
only thing it can see.

What this replaced is worth naming. The previous version kept a long-lived
container per project and drove it through the host's Docker socket. Beyond not
working in a cluster, mounting `docker.sock` into a pod gives whatever runs
there control of the host — it can start a privileged container and step
straight out. In a service whose entire job is running code a language model
wrote, that is the wrong default.
"""

from __future__ import annotations

import os
import shlex
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agents.shared.logging_setup import setup_logging
from apps.control_plane.adapters.k8s_sandbox import KubernetesSandbox
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = setup_logging("mcp-sandbox")

WORKSPACE = Path(os.environ.get("CREW_WORKSPACE", "/workspace"))
MAX_FILE_BYTES = 2 * 1024 * 1024

_sandbox: KubernetesSandbox | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    global _sandbox
    _sandbox = KubernetesSandbox(
        namespace=os.environ.get("CREW_NAMESPACE", "crew"),
        image=os.environ.get("CREW_AGENT_IMAGE", "crew-base:dev"),
    )
    await _sandbox.connect()
    yield
    await _sandbox.aclose()


app = FastAPI(title="MCP · sandbox", lifespan=lifespan)


def _project_dir(project_id: str) -> Path:
    """Resolve a project's directory, refusing anything outside it.

    `project_id` arrives from an agent, so it is untrusted input on a path.
    """
    base = (WORKSPACE / project_id).resolve()
    if not str(base).startswith(str(WORKSPACE.resolve())):
        raise HTTPException(400, "invalid project id")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve(project_id: str, relative: str) -> Path:
    """A path inside the project, or a 400.

    Without the containment check, `../../etc/passwd` is a valid `path`.
    """
    base = _project_dir(project_id)
    target = (base / relative.lstrip("/")).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(400, f"path escapes the project workspace: {relative}")
    return target


class WriteFile(BaseModel):
    project_id: str
    path: str
    content: str


class ReadFile(BaseModel):
    project_id: str
    path: str


class ExecCommand(BaseModel):
    project_id: str
    command: str
    cwd: str = "/workspace"
    timeout_s: int = 120


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "execution": "kubernetes-job"}


@app.post("/sandbox/write")
async def write_file(req: WriteFile) -> dict[str, Any]:
    """Write a file into the project's shared workspace."""
    if len(req.content.encode()) > MAX_FILE_BYTES:
        raise HTTPException(413, f"file exceeds {MAX_FILE_BYTES} bytes")
    target = _resolve(req.project_id, req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content)
    log.info("sandbox.write project_id=%s path=%s", req.project_id, req.path)
    return {"ok": True, "path": req.path, "bytes": len(req.content)}


@app.post("/sandbox/read")
async def read_file(req: ReadFile) -> dict[str, Any]:
    target = _resolve(req.project_id, req.path)
    if not target.is_file():
        raise HTTPException(404, f"no such file: {req.path}")
    return {"path": req.path, "content": target.read_text()}


@app.post("/sandbox/exec")
async def exec_command(req: ExecCommand) -> dict[str, Any]:
    """Run a command in a throwaway Job and return what it printed.

    The command string is split with `shlex`, not handed to a shell · a model
    that writes ``pytest; rm -rf /`` gets a file-not-found for a very oddly
    named test, rather than two commands.
    """
    assert _sandbox is not None
    try:
        argv = shlex.split(req.command)
    except ValueError as exc:
        raise HTTPException(400, f"could not parse command: {exc}") from exc
    if not argv:
        raise HTTPException(400, "empty command")

    handle = await _sandbox.create(req.project_id)
    result = await _sandbox.exec(handle, argv, timeout_s=req.timeout_s)
    log.info(
        "sandbox.exec project_id=%s argv=%s exit=%d",
        req.project_id,
        argv[0],
        result.exit_code,
    )
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timed_out": result.timed_out,
    }


@app.get("/sandbox/files/{project_id}")
async def list_files(project_id: str) -> dict[str, Any]:
    base = _project_dir(project_id)
    files = sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
    return {"project_id": project_id, "files": files}


@app.delete("/sandbox/{project_id}")
async def cleanup(project_id: str) -> dict[str, Any]:
    """Stop anything still executing for this project."""
    assert _sandbox is not None
    await _sandbox.destroy(await _sandbox.create(project_id))
    return {"project_id": project_id, "cleaned": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9003)
