"""mcp-sandbox · REAL code execution surface (Option B).

Each task gets ONE sandbox container · all agents on that task share it
(Backend writes files, Reviewer reads them, QA runs tests). Sandbox is
spawned on first write/exec request and cleaned up when the task closes.

Architecture:

    agent.write_file(task_id="task-x", path="api/foo.py", content="...")
        ↓
    mcp-sandbox.SandboxManager.get_or_create(task_id="task-x")
        ↓
    docker.containers.run(image="crew-sandbox:latest", ...) · the FIRST time only
        ↓
    docker exec crew-sandbox-task-x  · for every subsequent file write / command

Reliability: real Docker. No external API. Works offline. Latency is
predictable (single-host). Cohort can verify with `docker ps` they see
both agent containers AND per-task sandbox containers.

Endpoints:

    POST /sandbox/write    · write a file (creates sandbox if first call)
    POST /sandbox/read     · read a file
    POST /sandbox/exec     · run a command, get stdout/stderr/exit
    GET  /sandbox/files    · list files
    DELETE /sandbox/{task_id} · cleanup (called when task closes)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tarfile
import time
from typing import Any

import docker
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

app = FastAPI(title="mcp-sandbox", version="0.1.0")


SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "crew-sandbox:latest")
CREW_NETWORK = os.environ.get("CREW_NETWORK", "agentic-crew_crewnet")
# Absolute host path of the shared workspace · used as the bind source when
# spawning sibling sandbox containers via the host Docker daemon.
HOST_WORKSPACE = os.environ.get("HOST_WORKSPACE", "/workspace")


# ─── sandbox manager (real Docker) ───────────────────────────────────────

class SandboxManager:
    """Per-task sandbox container · created on first use, cleaned on close."""

    def __init__(self) -> None:
        self._docker = docker.from_env()
        self._task_containers: dict[str, str] = {}  # task_id → container_id

    def get_or_create(self, task_id: str) -> docker.models.containers.Container:
        # Reuse existing container for this task
        if task_id in self._task_containers:
            try:
                return self._docker.containers.get(self._task_containers[task_id])
            except docker.errors.NotFound:
                log.warning("sandbox.gone task=%s · recreating", task_id)
                del self._task_containers[task_id]

        # First request for this task · spawn a sandbox container
        name = f"crew-sandbox-{task_id}"
        log.info("sandbox.spawning task=%s image=%s", task_id, SANDBOX_IMAGE)
        try:
            container = self._docker.containers.run(
                image=SANDBOX_IMAGE,
                command="tail -f /dev/null",  # keep alive
                name=name,
                detach=True,
                network=CREW_NETWORK,
                working_dir="/workspace",
                # Mount the task root (NOT a /sandbox subdir) so commands here
                # run against the same files the agents write via their built-in
                # DeepAgents filesystem (FilesystemBackend rooted at the same
                # path). Source must be the HOST path · the host daemon resolves
                # bind sources against the host filesystem, not this container's.
                volumes={
                    f"{HOST_WORKSPACE}/{task_id}": {"bind": "/workspace", "mode": "rw"},
                },
                # Resource caps · keep sandbox from runaway
                mem_limit="1g",
                cpu_quota=100000,  # 1 CPU max
                # Auto-remove when stopped (cleanup safety net)
                auto_remove=False,  # we explicitly clean up in delete endpoint
            )
        except docker.errors.APIError as e:
            log.exception("sandbox.spawn_failed task=%s", task_id)
            raise HTTPException(500, f"sandbox spawn failed: {e}")

        self._task_containers[task_id] = container.id
        log.info("sandbox.ready task=%s container=%s",
                 task_id, container.short_id)
        return container

    def cleanup(self, task_id: str) -> bool:
        cid = self._task_containers.get(task_id)
        if not cid:
            return False
        try:
            container = self._docker.containers.get(cid)
            container.kill()
            container.remove()
        except docker.errors.NotFound:
            pass
        except Exception:
            log.exception("sandbox.cleanup_failed task=%s", task_id)
        del self._task_containers[task_id]
        return True

    def write_file(self, task_id: str, path: str, content: str) -> dict:
        container = self.get_or_create(task_id)

        # Docker put_archive needs a tar stream
        clean_path = path.lstrip("/")
        parent_dir = os.path.dirname(clean_path) or "."

        # Ensure parent directory exists
        if parent_dir != ".":
            self._exec_sync(container, f"mkdir -p {parent_dir}")

        # Put the file in via tar archive
        file_data = content.encode("utf-8")
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            info = tarfile.TarInfo(name=os.path.basename(clean_path))
            info.size = len(file_data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(file_data))
        tar_stream.seek(0)

        # Target directory inside the sandbox container
        target_dir = f"/workspace/{parent_dir}" if parent_dir != "." else "/workspace"
        container.put_archive(target_dir, tar_stream.read())

        log.info("sandbox.write task=%s path=%s bytes=%d",
                 task_id, path, len(content))
        return {"path": path, "bytes": len(content), "sandbox": container.short_id}

    def read_file(self, task_id: str, path: str) -> dict:
        container = self.get_or_create(task_id)
        result = container.exec_run(
            cmd=["cat", path],
            workdir="/workspace",
        )
        if result.exit_code != 0:
            return {"path": path, "content": "", "exists": False}
        return {
            "path": path,
            "content": result.output.decode("utf-8", errors="replace"),
            "exists": True,
        }

    def exec_command(self, task_id: str, command: str, cwd: str = "/workspace") -> dict:
        container = self.get_or_create(task_id)
        t0 = time.time()
        result = container.exec_run(
            cmd=["sh", "-c", command],
            workdir=cwd,
        )
        wall_clock_ms = int((time.time() - t0) * 1000)
        stdout = result.output.decode("utf-8", errors="replace")
        log.info("sandbox.exec task=%s cmd=%s exit=%d ms=%d",
                 task_id, command[:60], result.exit_code, wall_clock_ms)
        return {
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": "",  # docker exec merges streams in this client
            "wall_clock_ms": wall_clock_ms,
        }

    def list_files(self, task_id: str) -> list[str]:
        if task_id not in self._task_containers:
            return []
        result = self.exec_command(task_id, "find . -type f -not -path '*/\\.*'")
        return [l.strip("./").strip() for l in result["stdout"].splitlines() if l.strip()]

    def _exec_sync(self, container, command: str) -> tuple[int, str]:
        result = container.exec_run(cmd=["sh", "-c", command], workdir="/workspace")
        return result.exit_code, result.output.decode("utf-8", errors="replace")


_manager = SandboxManager()


# ─── request shapes ──────────────────────────────────────────────────────

class WriteFile(BaseModel):
    task_id: str
    path: str
    content: str


class ReadFile(BaseModel):
    task_id: str
    path: str


class ExecCommand(BaseModel):
    task_id: str
    command: str
    cwd: str = "/workspace"


# ─── routes ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "active_sandboxes": len(_manager._task_containers),
        "image": SANDBOX_IMAGE,
    }


@app.post("/sandbox/write")
async def write_file(req: WriteFile) -> dict:
    # docker SDK is sync; offload to a thread
    return await asyncio.to_thread(_manager.write_file, req.task_id, req.path, req.content)


@app.post("/sandbox/read")
async def read_file(req: ReadFile) -> dict:
    return await asyncio.to_thread(_manager.read_file, req.task_id, req.path)


@app.post("/sandbox/exec")
async def exec_command(req: ExecCommand) -> dict:
    return await asyncio.to_thread(_manager.exec_command, req.task_id, req.command, req.cwd)


@app.get("/sandbox/files/{task_id}")
async def list_files(task_id: str) -> dict:
    files = await asyncio.to_thread(_manager.list_files, task_id)
    return {"task_id": task_id, "files": files}


@app.delete("/sandbox/{task_id}")
async def cleanup(task_id: str) -> dict:
    cleaned = await asyncio.to_thread(_manager.cleanup, task_id)
    return {"task_id": task_id, "cleaned": cleaned}
