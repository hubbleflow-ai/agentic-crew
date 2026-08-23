"""The agent runtime · one DeepAgents harness per container.

Every crew container runs this. The role differs only in its system prompt and
its tool catalogue; the harness around them is identical, which is the point
the whole project is trying to make.

Two planes, kept apart deliberately:

* **The filesystem** is durable and shared. ``/workspace`` is the project's
  volume, mounted with ``subPath: <project-id>``, so files an engineer writes
  are still there for the reviewer — and a project cannot see another's work.
* **Redis** is ephemeral. It carries the live event stream and directed
  messages between agents. Nothing that matters is stored there.

What the agent *does* while it runs is reported by
:class:`~agents.shared.telemetry.TelemetryMiddleware`, not by this file. The
loop used to stream graph updates and reassemble them by hand; the middleware
sees each tool call and its result in one frame, so that code is gone.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from contracts.agent_env import WORKSPACE, AgentIdentity
from contracts.events import ACTIONABLE_KINDS, Event, EventKind, channel_for
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from agents.shared.logging_setup import bind_context, setup_logging
from agents.shared.telemetry import TelemetryMiddleware

log = setup_logging("agent")

IDLE_POLL_S = 5.0
"""A quiet channel is normal · a model is usually thinking, not gone."""

MAX_HISTORY_MESSAGES = 40
"""Durable memory is the filesystem. The transcript is only continuity, so it
is bounded rather than grown forever."""


# ─── the bus ─────────────────────────────────────────────────────────────


class AgentBus:
    """This agent's view of its project's channel.

    Publishes as ``role/name`` and filters out anything not addressed to it,
    using :meth:`contracts.events.Event.is_for` · the same rule the control
    plane applies, so neither side can drift into a delivery loop.
    """

    def __init__(self, identity: AgentIdentity) -> None:
        self.identity = identity
        self.channel = channel_for(identity.project_id)
        self._client: redis.Redis = redis.from_url(identity.redis_url)

    async def publish(self, event: Event) -> None:
        await self._client.publish(self.channel, json.dumps(asdict(event)))

    async def say(self, kind: EventKind, payload: dict[str, Any], *, to: str = "") -> None:
        """Publish something this agent is saying."""
        await self.publish(
            Event(
                project_id=self.identity.project_id,
                kind=kind,
                source=self.identity.address,
                payload=payload,
                to=to,
            )
        )

    async def listen(self, handle: Any, *, bootstrap: Event | None = None) -> None:
        """Deliver events to ``handle`` until cancelled.

        The bootstrap event is processed after the subscription is live but
        before the loop starts · it arrives by environment variable, not over
        the wire, so it can never be missed in the gap between the two.
        """
        pubsub = self._client.pubsub()
        await pubsub.subscribe(self.channel)
        log.info("agent.subscribed channel=%s", self.channel)

        if bootstrap is not None:
            await handle(bootstrap)

        while True:
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=IDLE_POLL_S
                )
            except RedisTimeoutError:
                continue  # idle, not broken
            except asyncio.CancelledError:
                raise
            except RedisConnectionError:
                log.warning("agent.redis_lost · retrying in 1s")
                await asyncio.sleep(1.0)
                continue

            if message is None or message.get("type") != "message":
                continue
            event = _decode(message["data"])
            if event is None or not event.is_for(self.identity.address):
                continue
            if event.kind not in ACTIONABLE_KINDS:
                continue  # telemetry is for the console, not a trigger
            await handle(event)

    async def aclose(self) -> None:
        await self._client.aclose()


def _decode(data: Any) -> Event | None:
    try:
        raw = json.loads(data.decode() if isinstance(data, bytes) else data)
        return Event(
            project_id=raw["project_id"],
            kind=EventKind(raw["kind"]),
            source=raw["source"],
            payload=raw.get("payload", {}),
            at=raw["at"],
            to=raw.get("to", ""),
        )
    except (ValueError, KeyError, TypeError):
        log.warning("agent.undecodable_event")
        return None


# ─── the harness ─────────────────────────────────────────────────────────


class Harness:
    """A DeepAgents agent, its model, and the middleware around it.

    ``create_deep_agent`` is a middleware assembler · the filesystem tools,
    the sub-agent tools and the planning tools all arrive as middleware. We
    add one of our own for telemetry and otherwise take the defaults, which is
    what "use the harness" means in practice.
    """

    def __init__(
        self,
        identity: AgentIdentity,
        system_prompt: str,
        bus: AgentBus,
        tools: list[Any],
    ) -> None:
        self.identity = identity
        self.history: list[Any] = []

        self.agent = create_deep_agent(
            model=ChatGoogleGenerativeAI(model=identity.model),
            system_prompt=system_prompt,
            tools=tools,
            backend=_filesystem_backend(),
            middleware=[TelemetryMiddleware(identity, bus)],
        )
        self.tool_names = [getattr(t, "__name__", str(t)) for t in tools]

        log.info(
            "harness.ready role=%s model=%s tools=%d workspace=%s",
            identity.role,
            identity.model,
            len(tools),
            WORKSPACE,
        )

    async def respond(self, event: Event, bus: AgentBus) -> None:
        """Take one turn.

        Everything observable about the turn — reasoning, tool calls, token
        spend — is published by the middleware while this runs. All that is
        left here is the final answer.
        """
        self.history.append(HumanMessage(content=_as_prompt(event)))

        try:
            result = await self.agent.ainvoke({"messages": self.history})
        except Exception as exc:
            log.exception("harness.turn_failed")
            await bus.say(EventKind.ERROR, {"context": "model_call", "error": str(exc)})
            return

        reply = _final_text(result)
        if reply:
            await bus.say(EventKind.AGENT_MESSAGE, {"text": reply})
            self.history.append(AIMessage(content=reply))

        # Keep only the turn boundaries · the working memory is on disk.
        if len(self.history) > MAX_HISTORY_MESSAGES:
            self.history = self.history[-MAX_HISTORY_MESSAGES:]


def _filesystem_backend() -> FilesystemBackend:
    """The project volume, as the agent's filesystem.

    ``virtual_mode`` roots every path the model uses at this directory, so a
    model that asks for ``/etc/passwd`` gets ``<workspace>/etc/passwd``.
    """
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    return FilesystemBackend(root_dir=WORKSPACE, virtual_mode=True)


def _as_prompt(event: Event) -> str:
    """Turn an event into something worth putting in front of a model."""
    text = event.payload.get("text") or event.payload.get("assignment") or ""
    body = text if text else json.dumps(event.payload, indent=2)
    return f"From {event.source} ({event.kind}):\n\n{body}"


def _final_text(result: dict[str, Any]) -> str:
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = [
                    str(p.get("text", ""))
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                joined = "\n".join(parts).strip()
                if joined:
                    return joined
    return ""


# ─── entry point ─────────────────────────────────────────────────────────


async def run_agent(system_prompt: str, tools: list[Any] | None = None) -> None:
    """Boot this container's agent and serve its project until stopped."""
    identity = AgentIdentity.from_env()
    bind_context(project_id=identity.project_id, role=identity.role, agent=identity.name)

    from agents.shared.observability import setup_observability

    setup_observability(identity.role)

    if tools is None:
        from agents.shared.agent_tools import build_role_tools

        tools = build_role_tools(identity)

    bus = AgentBus(identity)
    harness = Harness(identity, system_prompt, bus, tools)

    await bus.say(
        EventKind.AGENT_READY,
        {"role": identity.role, "agent": identity.name, "tools": harness.tool_names},
    )

    # The assignment arrives by environment, not over the wire · an agent that
    # booted a moment after its instructions were published would otherwise
    # sit idle forever waiting for a message that already went out.
    bootstrap = None
    if identity.assignment:
        bootstrap = Event(
            project_id=identity.project_id,
            kind=EventKind.ASSIGNMENT,
            source="control-plane",
            payload={"assignment": identity.assignment},
            to=identity.address,
        )

    log.info("agent.starting role=%s project_id=%s", identity.role, identity.project_id)

    async def handle(event: Event) -> None:
        try:
            await harness.respond(event, bus)
        except Exception:
            log.exception("agent.handler_failed")
            await bus.say(EventKind.ERROR, {"context": "handler"})

    try:
        await bus.listen(handle, bootstrap=bootstrap)
    finally:
        await bus.aclose()
