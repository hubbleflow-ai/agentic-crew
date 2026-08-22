"""Shared agent runtime · a DeepAgents + Gemini brain per container.

Each agent container runs ONE DeepAgents agent (LangChain `create_deep_agent`)
powered by Gemini. The agent's filesystem is a real, persistent directory on
the shared `/workspace` volume (DeepAgents `FilesystemBackend`), so files the
agent writes are durable and visible to every other agent container.

Data split (see also the project notes):
  * Filesystem (durable, shared)  · work artifacts, ticket, notes/memory.
  * Redis (ephemeral)             · the live UI event stream + inter-agent
                                     signalling. Never source-of-truth state.

The loop:

    1. Subscribe to the task's Redis channel.
    2. On each incoming message (or the bootstrap founder request):
       a. Skip self-sent messages.
       b. Format the envelope as a user message for the agent.
       c. `agent.ainvoke(...)` · Gemini reasons, calls built-in filesystem /
          planning tools (and any custom tools we pass), all in-process via
          DeepAgents.
       d. Walk the new messages · publish reasoning / tool_call / usage /
          response events to the bus so the UI can render them.
    3. Repeat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from deepagents import create_deep_agent

# FilesystemBackend lives at the top level in current deepagents; fall back to
# the submodule path on older/newer layouts, and degrade to the default
# (in-state) backend if it can't be imported at all.
try:  # pragma: no cover - import shape varies by version
    from deepagents import FilesystemBackend
except Exception:  # pragma: no cover
    try:
        from deepagents.backends import FilesystemBackend
    except Exception:
        FilesystemBackend = None  # type: ignore[assignment]

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI

log = logging.getLogger(__name__)


# ─── env context ─────────────────────────────────────────────────────────

class AgentContext:
    """Convenience accessor for env vars the control plane injects."""

    def __init__(self) -> None:
        self.role: str = os.environ["CREW_ROLE"]
        self.task_id: str = os.environ["CREW_TASK_ID"]
        self.agent_id: str = os.environ["CREW_AGENT_ID"]
        self.redis_url: str = os.environ["REDIS_URL"]
        # Gemini · langchain-google-genai reads GOOGLE_API_KEY from the env.
        self.google_api_key: str = os.environ.get("GOOGLE_API_KEY", "")
        self.model: str = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

    @property
    def channel(self) -> str:
        return f"crew/task/{self.task_id}/messages"

    @property
    def from_id(self) -> str:
        return f"{self.role}/{self.agent_id}"


# ─── event bus ───────────────────────────────────────────────────────────

class EventBus:
    """Redis pub/sub wrapper · publish & subscribe on the task channel.

    This is the EPHEMERAL plane: the live UI stream + inter-agent signalling.
    No durable state lives here — that's all on the filesystem volume.
    """

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx
        self._client = redis.from_url(ctx.redis_url)

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        envelope = {
            "from": self.ctx.from_id,
            "type": event_type,
            "at": time.time(),
            "payload": payload,
        }
        await self._client.publish(self.ctx.channel, json.dumps(envelope))
        log.info("event.published type=%s", event_type)

    async def publish_component_update(self, components: list[dict[str, Any]]) -> None:
        """Tell the UI what this agent's workspace currently looks like."""
        await self.publish("component_update", {"active_components": components})

    async def subscribe(self, handler, bootstrap: dict | None = None) -> None:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(self.ctx.channel)
        log.info("subscribed channel=%s", self.ctx.channel)
        # Process the bootstrap message (founder_request / work_assigned)
        # BEFORE entering the listen loop. This is race-free · the message
        # is injected via env, not pub/sub, so it can never be missed.
        if bootstrap is not None:
            log.info("bootstrap.processing type=%s", bootstrap.get("type"))
            await handler(bootstrap)
        # Resilient receive loop. A blocking pub/sub read can raise a socket
        # read TimeoutError when the agent is idle (no peer messages) · that is
        # NORMAL, not fatal. Poll with a timeout and keep going so idle agents
        # survive until they're needed again.
        while True:
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=5.0
                )
            except RedisTimeoutError:
                continue  # idle · just keep waiting
            except asyncio.CancelledError:
                raise  # genuine shutdown
            except RedisConnectionError:
                log.warning("subscribe.connection_lost · retrying in 1s")
                await asyncio.sleep(1.0)
                continue
            except Exception:
                log.exception("subscribe.read_error · retrying in 1s")
                await asyncio.sleep(1.0)
                continue

            if message is None or message.get("type") != "message":
                continue
            try:
                envelope = json.loads(message["data"])
            except (ValueError, KeyError, TypeError):
                continue
            if envelope.get("from") == self.ctx.from_id:
                continue  # ignore self
            await handler(envelope)

    async def close(self) -> None:
        await self._client.aclose()


# ─── workspace ───────────────────────────────────────────────────────────

class Workspace:
    """Sectioned-ownership doc store on the shared, persistent volume.

    This is the DURABLE plane. The DeepAgents FilesystemBackend is rooted at
    the same `/workspace/<task_id>` directory, so the agent's built-in
    write_file/read_file land here too · one shared, persistent disk.
    """

    def __init__(self, ctx: AgentContext) -> None:
        self.base = Path(f"/workspace/{ctx.task_id}")
        self.base.mkdir(parents=True, exist_ok=True)
        self.section = self.base / f"section_{ctx.role}.md"

    def write_section(self, body: str) -> None:
        self.section.write_text(body)

    def read_all_sections(self) -> dict[str, str]:
        return {
            p.stem.replace("section_", ""): p.read_text()
            for p in self.base.glob("section_*.md")
        }


# ─── system prompt loader ────────────────────────────────────────────────

def load_system_prompt(agent_module_path: str) -> str:
    return (Path(agent_module_path).parent / "system_prompt.md").read_text()


# ─── helpers ──────────────────────────────────────────────────────────────

def _content_text(content: Any) -> str:
    """LangChain message content is a str or a list of parts · flatten to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                # Gemini text parts look like {"type": "text", "text": "..."}
                if p.get("type") == "text" and p.get("text"):
                    parts.append(str(p["text"]))
        return "\n".join(parts)
    return str(content)


# ─── DeepAgents + Gemini runner ──────────────────────────────────────────

class DeepAgentRunner:
    """Runs a DeepAgents agent (Gemini) with a persistent filesystem backend.

    Conversation history is kept in-process (`self.history`) and replayed each
    turn so the agent has continuity across peer messages.
    """

    def __init__(self, ctx: AgentContext, system_prompt: str, tools: list | None = None) -> None:
        self.ctx = ctx
        self.system_prompt = system_prompt
        tools = tools or []
        self.tool_names = [getattr(t, "__name__", str(t)) for t in tools]

        # Persistent, shared filesystem rooted on the workspace volume.
        root = f"/workspace/{ctx.task_id}"
        Path(root).mkdir(parents=True, exist_ok=True)
        backend = None
        if FilesystemBackend is not None:
            try:
                backend = FilesystemBackend(root_dir=root, virtual_mode=True)
            except Exception:
                log.exception("filesystem_backend.init_failed · falling back to state backend")

        # Build the Gemini chat model directly · avoids depending on
        # langchain's init_chat_model / provider-string resolver, and matches
        # how Session 6 wires Gemini. Reads GOOGLE_API_KEY from the env if not
        # passed explicitly.
        llm = ChatGoogleGenerativeAI(
            model=ctx.model,
            google_api_key=ctx.google_api_key or None,
        )
        kwargs: dict[str, Any] = {"model": llm, "tools": tools}
        if backend is not None:
            kwargs["backend"] = backend

        # The system-prompt kwarg has been named both `system_prompt` and
        # `instructions` across deepagents versions · try both.
        try:
            self.agent = create_deep_agent(system_prompt=system_prompt, **kwargs)
        except TypeError:
            self.agent = create_deep_agent(instructions=system_prompt, **kwargs)

        self.history: list[Any] = []
        log.info(
            "deepagent.ready role=%s model=%s backend=%s tools=%d",
            ctx.role, ctx.model, "filesystem" if backend is not None else "state",
            len(tools),
        )

    async def handle_envelope(self, envelope: dict, bus: EventBus) -> None:
        """Process a peer's (or the founder's) message via the DeepAgents agent."""
        sender = envelope.get("from", "unknown")
        event_type = envelope.get("type", "message")
        payload = envelope.get("payload", {})

        user_content = (
            f"Message from {sender} · type={event_type}\n"
            f"Payload:\n{json.dumps(payload, indent=2)}"
        )
        self.history.append(HumanMessage(content=user_content))

        log.info("deepagent.streaming role=%s msgs=%d", self.ctx.role, len(self.history))
        in_tok = out_tok = 0
        final_text = ""
        seen: set[str] = set()
        try:
            # Stream node updates so each reasoning step / tool call surfaces in
            # the UI the INSTANT the agent makes it — "who is checking what",
            # live — instead of all batched at the end of the turn.
            async for chunk in self.agent.astream(
                {"messages": self.history}, stream_mode="updates"
            ):
                for _node, update in (chunk or {}).items():
                    if not isinstance(update, dict):
                        continue
                    for m in update.get("messages", []) or []:
                        mid = getattr(m, "id", None)
                        if mid and mid in seen:
                            continue
                        if mid:
                            seen.add(mid)
                        if isinstance(m, AIMessage):
                            um = getattr(m, "usage_metadata", None) or {}
                            in_tok += int(um.get("input_tokens", 0) or 0)
                            out_tok += int(um.get("output_tokens", 0) or 0)
                            text = _content_text(m.content)
                            if text.strip():
                                await bus.publish("reasoning", {"text": text})
                                final_text = text
                            for tc in (getattr(m, "tool_calls", None) or []):
                                await bus.publish("tool_call", {
                                    "tool": tc.get("name", "tool"),
                                    "input": tc.get("args", {}),
                                    "result_preview": "",
                                })
                        elif isinstance(m, ToolMessage):
                            await bus.publish("tool_call", {
                                "tool": getattr(m, "name", "tool"),
                                "input": {},
                                "result_preview": _content_text(m.content)[:200],
                            })
        except Exception as e:
            log.exception("deepagent.stream_failed")
            await bus.publish("error", {"context": "gemini_call", "error": str(e)})
            return

        if in_tok or out_tok:
            await bus.publish("usage", {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "model": self.ctx.model,
            })
        if final_text.strip():
            await bus.publish("response", {"text": final_text})
            # Persist a COMPACT history · the user turn + final reply only.
            # Durable memory lives in the filesystem, not the transcript; this
            # bounds token replay across turns.
            self.history.append(AIMessage(content=final_text))
        if len(self.history) > 40:
            self.history = self.history[-40:]


# ─── main entry point ────────────────────────────────────────────────────

async def run_agent_loop(
    ctx: AgentContext,
    system_prompt: str,
    on_message_hook=None,
    tools: list | None = None,
) -> None:
    """The main loop. Subscribes to Redis, hands every message to the agent.

    `tools` (optional) is a list of plain Python functions to expose to the
    DeepAgents agent in addition to its built-in filesystem/planning tools.
    `on_message_hook` (optional) lets a specific agent intercept messages
    before the agent sees them.
    """
    # Phoenix tracing · every Gemini/LangGraph call + MCP hop becomes a span
    # (no-op if PHOENIX_COLLECTOR_ENDPOINT is unset).
    from agents.shared.observability import setup_observability
    setup_observability(ctx.role)

    # Default to the role's tool catalogue (MCP wrappers + spawn/escalate),
    # bound to this agent's task_id/agent_id. Engineers also get DeepAgents'
    # built-in filesystem tools for free.
    if tools is None:
        from agents.shared.agent_tools import build_role_tools
        tools = build_role_tools(ctx)

    bus = EventBus(ctx)
    workspace = Workspace(ctx)
    runner = DeepAgentRunner(ctx, system_prompt, tools=tools)

    log.info("agent.starting role=%s task=%s agent_id=%s",
             ctx.role, ctx.task_id, ctx.agent_id)

    # The control plane may hand us a first task via env · the Founder's
    # request (EM) or an EM assignment (workers). Parse it now; we process
    # it inside subscribe(), after the channel subscription is live.
    bootstrap_env: dict | None = None
    raw_bootstrap = os.environ.get("CREW_BOOTSTRAP_MESSAGE")
    if raw_bootstrap:
        try:
            bootstrap_env = json.loads(raw_bootstrap)
        except ValueError:
            log.warning("bootstrap.parse_failed · ignoring malformed CREW_BOOTSTRAP_MESSAGE")

    # Announce ourselves
    await bus.publish("intro", {
        "role": ctx.role,
        "agent_id": ctx.agent_id,
        "tools_available": runner.tool_names,
    })

    # Only these envelope types DRIVE an agent's work. UI/telemetry events
    # (reasoning, tool_call, usage, response, intro, ...) are for the console,
    # NOT triggers — reacting to them caused a broadcast feedback storm where
    # every agent re-invoked on every other agent's output, exhausting the LLM
    # quota. Directed messaging only.
    ACTIONABLE = {"founder_request", "founder_message", "work_assigned", "message"}

    async def handle(envelope: dict) -> None:
        etype = envelope.get("type")
        if etype not in ACTIONABLE:
            return
        # founder_request is the EM's bootstrap (the PM isn't spawned yet).
        if etype == "founder_request" and ctx.role != "engineering_manager":
            return
        # Founder chat reaches the two Founder-facing roles: the EM
        # (orchestration) and the PM (requirement clarification). Workers act
        # on their assignment, not on Founder chatter.
        if etype == "founder_message" and ctx.role not in ("engineering_manager", "product_manager"):
            return
        try:
            if on_message_hook:
                await on_message_hook(envelope, bus, workspace)
            await runner.handle_envelope(envelope, bus)
        except Exception:
            log.exception("agent.handler_error")
            await bus.publish("error", {"context": "handler"})

    try:
        await bus.subscribe(handle, bootstrap=bootstrap_env)
    finally:
        await bus.close()
