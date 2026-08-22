"""control-plane · spawns and kills agent containers, relays Redis pub/sub
to the browser via WebSocket. Enforces spawn caps server-side.

Endpoints:

    POST /tasks                  · create a new task (returns task_id)
    GET  /tasks/{task_id}        · task state
    POST /agents/spawn           · spawn an agent for a task (enforces cap)
    GET  /agents                 · list active agents
    DELETE /agents/{agent_id}    · kill an agent container
    POST /tickets                · create/update a task ticket (EM's source-of-truth)
    GET  /tickets/{ticket_id}    · read the ticket (other agents call this)
    WS   /stream/{task_id}       · live updates · relays Redis pub/sub
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import docker
import redis.asyncio as redis
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from redis.exceptions import TimeoutError as RedisTimeoutError

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


# ─── spawn limits (loaded from config) ───────────────────────────────────

_LIMITS_PATH = Path(__file__).parent / "spawn_limits.yaml"
with _LIMITS_PATH.open() as fh:
    _CONFIG = yaml.safe_load(fh)
SPAWN_LIMITS: dict[str, int] = _CONFIG["limits"]
TOTAL_CAP: int = _CONFIG["total_per_task"]


# ─── role → agent module mapping ─────────────────────────────────────────
# Spawn caps use canonical role names (backend_engineer); the agent code
# lives under shorter dir names (agents/backend). This bridges the two.
# A role in SPAWN_LIMITS without an entry here has no agent implementation
# yet and cannot be spawned.

ROLE_MODULES: dict[str, str] = {
    "engineering_manager": "em",
    "product_manager": "pm",
    "backend_engineer": "backend",
    "frontend_engineer": "frontend",
    "qa_engineer": "qa",
    "code_reviewer": "reviewer",
}


# ─── in-memory state (Postgres in v1.1) ──────────────────────────────────
# For v1 we keep state in process. v1.1 persists to Postgres for durability.

_agents: dict[str, dict[str, Any]] = {}  # agent_id → metadata
_tasks: dict[str, dict[str, Any]] = {}   # task_id → metadata
_tickets: dict[str, dict[str, Any]] = {} # ticket_id → ticket body

# Redis + Docker clients (initialized in lifespan)
_redis: redis.Redis | None = None
_docker: docker.DockerClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis, _docker
    log.info("control-plane.starting")
    _redis = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    _docker = docker.from_env()
    log.info("control-plane.ready · spawn_limits=%s", SPAWN_LIMITS)
    yield
    log.info("control-plane.stopping")
    if _redis:
        await _redis.aclose()


app = FastAPI(title="crew-control-plane", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── request models ──────────────────────────────────────────────────────

class TaskCreateRequest(BaseModel):
    request: str           # the founder's natural language input
    scenario: str = "build"  # build | investigate | refactor
    local_hour: int | None = None  # Founder's local hour 0-23 (for greeting)


class SpawnRequest(BaseModel):
    task_id: str
    role: str              # engineering_manager | backend_engineer | ...
    requested_by: str = "founder"
    override_cap: bool = False  # requires founder approval
    bootstrap_message: str | None = None  # JSON envelope delivered on boot


class EscalationRequest(BaseModel):
    task_id: str
    question: str
    context: str = ""
    options: list[str] = []


class FounderMessageRequest(BaseModel):
    text: str
    local_hour: int | None = None  # Founder's local hour 0-23 (for greeting)


class TicketWriteRequest(BaseModel):
    task_id: str
    body: dict[str, Any]


# ─── helpers ──────────────────────────────────────────────────────────────

def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def _greeting(hour: int | None) -> str:
    """Time-appropriate greeting from the Founder's local hour (0-23)."""
    if hour is None or not (0 <= hour <= 23):
        return "Hello"
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 22:
        return "Good evening"
    return "Hello"  # late night / very early


def _agent_role_count(task_id: str, role: str) -> int:
    return sum(
        1 for a in _agents.values()
        if a["task_id"] == task_id and a["role"] == role and a["status"] != "killed"
    )


def _agent_total_count(task_id: str) -> int:
    return sum(1 for a in _agents.values() if a["task_id"] == task_id and a["status"] != "killed")


# ─── routes ──────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "spawn_limits": SPAWN_LIMITS,
        "active_agents": sum(1 for a in _agents.values() if a["status"] != "killed"),
        "active_tasks": len(_tasks),
    }


@app.post("/tasks")
async def create_task(req: TaskCreateRequest) -> dict:
    task_id = _new_id("task")
    task = {
        "task_id": task_id,
        "scenario": req.scenario,
        "request": req.request,
        "created_at": time.time(),
        "status": "spawning_em",
    }
    _tasks[task_id] = task
    log.info("task.created task_id=%s scenario=%s", task_id, req.scenario)

    # Auto-spawn the Engineering Manager and hand it the Founder's request
    # as a race-free bootstrap message. The EM drives everything from here:
    # consults the PM, authors the ticket, spawns the specialist team.
    bootstrap = json.dumps({
        "from": "founder",
        "type": "founder_request",
        "payload": {
            "task_id": task_id,
            "request": req.request,
            "scenario": req.scenario,
            "greeting": _greeting(req.local_hour),
        },
    })
    try:
        em = _do_spawn(
            task_id=task_id,
            role="engineering_manager",
            requested_by="founder",
            bootstrap_message=bootstrap,
        )
        task["em_agent_id"] = em["agent_id"]
        task["status"] = "em_running"
    except HTTPException as e:
        log.error("task.em_spawn_failed task_id=%s detail=%s", task_id, e.detail)
        task["status"] = "em_spawn_failed"
        task["error"] = e.detail

    return task


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@app.post("/tasks/{task_id}/message")
async def post_founder_message(task_id: str, req: FounderMessageRequest) -> dict:
    """The Founder sends a message into a live task · published on the task
    channel so every agent (and the UI) sees it. This is how follow-ups and
    escalation answers reach the crew."""
    if task_id not in _tasks:
        raise HTTPException(404, "task not found")
    envelope = {
        "from": "founder",
        "type": "founder_message",
        "at": time.time(),
        # No greeting on follow-up messages · the EM greets ONCE on the very
        # first message (the founder_request). Re-greeting was a bug.
        "payload": {"text": req.text},
    }
    await _redis.publish(f"crew/task/{task_id}/messages", json.dumps(envelope))
    log.info("founder.message task_id=%s len=%d", task_id, len(req.text))
    return {"ok": True}


# ─── task teardown · kill containers + wipe files (no orphan cruft) ───────

def _teardown_task(task_id: str, wipe_files: bool = True) -> dict:
    """Kill a task's agent + sandbox containers and (optionally) wipe its
    workspace dir + per-agent browser evidence. Idempotent."""
    killed: list[str] = []
    for aid, a in list(_agents.items()):
        if a["task_id"] != task_id:
            continue
        if a.get("status") != "killed":
            try:
                c = _docker.containers.get(a["container_id"])
                c.kill()
                c.remove()
            except Exception:
                pass
            a["status"] = "killed"
            killed.append(a["container_name"])
        if wipe_files:
            shutil.rmtree(Path(f"/workspace/evidence/{aid}"), ignore_errors=True)

    # The per-task sandbox container is spawned by mcp-sandbox (not tracked
    # here) and named crew-sandbox-<task_id> · kill it by name.
    try:
        sb = _docker.containers.get(f"crew-sandbox-{task_id}")
        sb.kill()
        sb.remove()
        killed.append(f"crew-sandbox-{task_id}")
    except Exception:
        pass

    files_removed = 0
    if wipe_files:
        ws = Path(f"/workspace/{task_id}")
        if ws.exists():
            files_removed = sum(1 for p in ws.rglob("*") if p.is_file())
            shutil.rmtree(ws, ignore_errors=True)

    _tasks.pop(task_id, None)
    log.info("task.torn_down task_id=%s killed=%d files=%d", task_id, len(killed), files_removed)
    return {"task_id": task_id, "killed": killed, "files_removed": files_removed}


@app.delete("/tasks/{task_id}")
async def teardown_task(task_id: str, wipe_files: bool = True) -> dict:
    """Tear down a task · kill its agent + sandbox containers and (by default)
    wipe its workspace files. Pass ?wipe_files=false to keep the artifacts.
    This is what a UI 'clear task' button calls."""
    known = task_id in _tasks or any(a["task_id"] == task_id for a in _agents.values())
    if not known:
        raise HTTPException(404, "task not found")
    return _teardown_task(task_id, wipe_files=wipe_files)


@app.delete("/tasks")
async def teardown_all_tasks(wipe_files: bool = True) -> dict:
    """Tear down ALL tasks · a clean slate (containers killed, files wiped)."""
    task_ids = set(_tasks) | {a["task_id"] for a in _agents.values()}
    results = [_teardown_task(t, wipe_files=wipe_files) for t in task_ids]
    return {"torn_down": len(results), "tasks": results}


def _do_spawn(
    task_id: str,
    role: str,
    requested_by: str = "founder",
    override_cap: bool = False,
    bootstrap_message: str | None = None,
) -> dict:
    """Spawn one agent container for a task. Enforces spawn caps
    server-side. Raises HTTPException on any rejection or failure.

    Shared by the /agents/spawn endpoint and the EM auto-spawn in
    create_task, so cap enforcement and env injection live in one place.
    """
    if task_id not in _tasks:
        raise HTTPException(404, "task not found")

    if role not in SPAWN_LIMITS:
        raise HTTPException(400, f"unknown role: {role}")

    module = ROLE_MODULES.get(role)
    if module is None:
        raise HTTPException(400, f"no agent implementation for role: {role}")

    # Enforce per-role cap
    current = _agent_role_count(task_id, role)
    cap = SPAWN_LIMITS[role]
    if current >= cap and not override_cap:
        raise HTTPException(
            409,
            f"spawn cap reached for {role} · {current}/{cap}. "
            "Request override with override_cap=true (requires founder approval).",
        )

    # Enforce total cap
    total = _agent_total_count(task_id)
    if total >= TOTAL_CAP and not override_cap:
        raise HTTPException(
            409, f"task total cap reached · {total}/{TOTAL_CAP}",
        )

    agent_id = _new_id(role.split("_")[0])
    container_name = f"crew-{agent_id}"

    # Spawn the container · in v1 this is a real docker run
    image = os.environ.get("CREW_AGENT_IMAGE", "crew-agent:latest")
    network = os.environ.get("CREW_NETWORK", "agentic-crew_crewnet")

    environment = {
        # Identity
        "CREW_ROLE": role,
        "CREW_TASK_ID": task_id,
        "CREW_AGENT_ID": agent_id,
        # Infra
        "REDIS_URL": os.environ["REDIS_URL"],
        # Gemini · langchain-google-genai reads GOOGLE_API_KEY from the env.
        "GOOGLE_API_KEY": os.environ.get("GOOGLE_API_KEY", ""),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        # Phoenix tracing · so each agent's spans land in the same project
        "PHOENIX_COLLECTOR_ENDPOINT": os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", ""),
        "LANGSMITH_API_KEY": os.environ.get("LANGSMITH_API_KEY", ""),
        # So the EM's spawn_agent / escalate_to_founder tools can call home
        "CONTROL_PLANE_URL": os.environ.get("CONTROL_PLANE_URL", "http://control-plane:8001"),
        # MCP server URLs · service-discoverable on crewnet
        "MCP_TICKETS_URL": "http://mcp-tickets:9001",
        "MCP_GITHUB_URL":  "http://mcp-github:9002",
        "MCP_SANDBOX_URL": "http://mcp-sandbox:9003",
        "MCP_BROWSER_URL": "http://mcp-browser:9004",
        "MCP_JIRA_URL":    "http://mcp-jira:9005",
        # Logging
        "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO"),
    }
    # Race-free first task · processed by the agent loop the moment it boots
    if bootstrap_message:
        environment["CREW_BOOTSTRAP_MESSAGE"] = bootstrap_message

    try:
        container = _docker.containers.run(
            image=image,
            command=f"python -m agents.{module}.main",
            name=container_name,
            detach=True,
            network=network,
            environment=environment,
            volumes={
                # Shared workspace · all agents in this task see the same files.
                # Source must be the HOST path (the host daemon runs this), not
                # our own container's /workspace.
                os.environ.get("HOST_WORKSPACE", "/workspace"): {
                    "bind": "/workspace", "mode": "rw",
                },
            },
        )
        log.info("agent.spawned agent_id=%s role=%s module=%s container=%s",
                 agent_id, role, module, container.short_id)
    except Exception as e:
        log.exception("agent.spawn_failed role=%s", role)
        raise HTTPException(500, f"failed to spawn container: {e}")

    _agents[agent_id] = {
        "agent_id": agent_id,
        "task_id": task_id,
        "role": role,
        "container_name": container_name,
        "container_id": container.id,
        "status": "starting",
        "started_at": time.time(),
        "tokens_used": 0,
        "requested_by": requested_by,
    }

    return _agents[agent_id]


@app.post("/agents/spawn")
async def spawn_agent(req: SpawnRequest) -> dict:
    """Spawn an agent container. Enforces spawn caps server-side."""
    return _do_spawn(
        task_id=req.task_id,
        role=req.role,
        requested_by=req.requested_by,
        override_cap=req.override_cap,
        bootstrap_message=req.bootstrap_message,
    )


@app.get("/agents")
def list_agents(task_id: str | None = None) -> list[dict]:
    items = list(_agents.values())
    if task_id:
        items = [a for a in items if a["task_id"] == task_id]
    return items


@app.delete("/agents/{agent_id}")
async def kill_agent(agent_id: str) -> dict:
    agent = _agents.get(agent_id)
    if not agent:
        raise HTTPException(404, "agent not found")
    try:
        container = _docker.containers.get(agent["container_id"])
        container.kill()
        container.remove()
    except Exception:
        log.exception("agent.kill_failed agent_id=%s", agent_id)
    agent["status"] = "killed"
    return agent


# ─── ticket-driven workflow (EM authors, others read) ───────────────────

@app.post("/tickets")
def write_ticket(req: TicketWriteRequest) -> dict:
    ticket_id = f"TICKET-{req.task_id.split('-')[1]}"
    _tickets[ticket_id] = {
        "ticket_id": ticket_id,
        "task_id": req.task_id,
        "body": req.body,
        "updated_at": time.time(),
    }
    log.info("ticket.written ticket_id=%s", ticket_id)
    return _tickets[ticket_id]


@app.get("/tickets/{ticket_id}")
def read_ticket(ticket_id: str) -> dict:
    ticket = _tickets.get(ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    return ticket


# ─── escalations (HITL gate) ─────────────────────────────────────────────

@app.post("/escalations")
async def create_escalation(req: EscalationRequest) -> dict:
    """An agent raises a decision for the Founder. We publish it on the task
    channel (so the UI/CLI surfaces it) and acknowledge immediately so the
    crew is never hard-blocked in a headless run · a real Founder reply can
    later arrive on the same channel as a normal message."""
    if req.task_id not in _tasks:
        raise HTTPException(404, "task not found")

    envelope = {
        "from": "control-plane",
        "type": "escalation",
        "at": time.time(),
        "payload": {
            "question": req.question,
            "context": req.context,
            "options": req.options,
        },
    }
    await _redis.publish(f"crew/task/{req.task_id}/messages", json.dumps(envelope))
    log.info("escalation.raised task_id=%s q=%s", req.task_id, req.question[:80])
    return {
        "acknowledged": True,
        "status": "awaiting_founder",
        "guidance": (
            "Escalation surfaced to the Founder. If no human responds, proceed "
            "with your best judgement and note the assumption in the ticket."
        ),
    }


# ─── WebSocket · live agent events ───────────────────────────────────────

@app.websocket("/stream/{task_id}")
async def stream(ws: WebSocket, task_id: str):
    """Relay Redis pub/sub to the browser."""
    await ws.accept()
    pubsub = _redis.pubsub()
    await pubsub.subscribe(f"crew/task/{task_id}/messages")
    try:
        while True:
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=5.0
                )
            except RedisTimeoutError:
                continue  # idle · the relay stays open
            if message is None or message.get("type") != "message":
                continue
            data = message["data"]
            await ws.send_text(data.decode() if isinstance(data, bytes) else data)
    except (WebSocketDisconnect, asyncio.CancelledError):
        log.info("ws.disconnected task_id=%s", task_id)
    except Exception:
        # Browser closing the socket surfaces as a connection reset on the
        # blocked read · that's an expected disconnect, not an error.
        log.info("ws.closed task_id=%s", task_id)
    finally:
        try:
            await pubsub.unsubscribe(f"crew/task/{task_id}/messages")
            await pubsub.aclose()
        except Exception:
            pass
