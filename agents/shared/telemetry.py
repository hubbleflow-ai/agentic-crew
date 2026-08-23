"""Telemetry middleware · the harness reports on itself.

Before this, the agent loop called ``astream(stream_mode="updates")`` and
walked the resulting message dicts by hand, guessing which were new, matching
tool calls to their results by position, and de-duplicating on message ids. It
worked, mostly. It also had to be re-read every time the graph changed shape.

A middleware asks nothing of the caller. ``wrap_tool_call`` sits *around* every
tool the agent invokes, so it sees the call and its result as one thing, with
no correlation to reconstruct. ``after_model`` sees each model response once,
so nothing needs de-duplicating.

This is also the clearest example of what a harness *is*: the model did not
change, and the agent gained an observable trail.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from contracts.agent_env import AgentIdentity
from contracts.events import Event, EventKind
from langchain.agents.middleware import AgentMiddleware, AgentState, Runtime
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langgraph.types import Command

from agents.shared.logging_setup import setup_logging

log = setup_logging("telemetry")

RESULT_PREVIEW_CHARS = 300
"""Enough to see what a tool returned, short enough not to flood the UI."""


@runtime_checkable
class EventPublisher(Protocol):
    """All the middleware needs from a bus.

    A Protocol rather than a base class, so the real Redis bus and a list that
    records events are equally valid here and neither has to inherit anything.
    """

    async def publish(self, event: Event) -> None: ...


class TelemetryMiddleware(AgentMiddleware):
    """Publishes what the agent thinks, calls, and spends.

    Attached to every role. It adds no tools and changes no behaviour · it
    only reports, which is why it is safe to have on always.
    """

    def __init__(self, identity: AgentIdentity, bus: EventPublisher) -> None:
        super().__init__()
        self.identity = identity
        self.bus = bus

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Announce a tool call, run it, then announce what came back.

        Wrapping is what makes the pairing free: the call and its result are
        the same stack frame, so nothing has to match them up afterwards.
        """
        name = _tool_name(request)
        started = time.perf_counter()

        await self._emit(
            EventKind.TOOL_CALL,
            {"tool": name, "input": _tool_args(request), "status": "started"},
        )

        try:
            result = await handler(request)
        except Exception as exc:
            # A tool that raises still gets reported · a call that vanishes
            # from the trail is worse than one that failed loudly.
            await self._emit(
                EventKind.TOOL_CALL,
                {"tool": name, "status": "error", "error": str(exc)},
            )
            raise

        await self._emit(
            EventKind.TOOL_CALL,
            {
                "tool": name,
                "status": "finished",
                "ms": round((time.perf_counter() - started) * 1000),
                "result_preview": _preview(result),
            },
        )
        return result

    async def aafter_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Report the model's own output · reasoning and token spend.

        Called once per model response, so there is nothing to de-duplicate.
        """
        messages = state.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        text = _text(last.content)
        if text.strip():
            await self._emit(EventKind.AGENT_THINKING, {"text": text})

        usage = getattr(last, "usage_metadata", None) or {}
        if usage:
            await self._emit(
                EventKind.USAGE,
                {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "model": self.identity.model,
                },
            )
        # No state change · this middleware observes, it never rewrites.
        return None

    async def _emit(self, kind: EventKind, payload: dict[str, Any]) -> None:
        """Publish, and never let telemetry break the agent.

        An agent that dies because its event bus hiccuped is a worse outcome
        than a gap in the trail.
        """
        try:
            await self.bus.publish(
                Event(
                    project_id=self.identity.project_id,
                    kind=kind,
                    source=self.identity.address,
                    payload=payload,
                )
            )
        except Exception:
            log.warning("telemetry.publish_failed kind=%s", kind, exc_info=True)


# ─── shape helpers · LangChain hands these back in several forms ─────────


def _tool_name(request: ToolCallRequest) -> str:
    call: ToolCall | None = getattr(request, "tool_call", None)
    if call is not None:
        return str(call.get("name", "tool"))
    return "tool"


def _tool_args(request: ToolCallRequest) -> dict[str, Any]:
    call: ToolCall | None = getattr(request, "tool_call", None)
    if call is not None and isinstance(call.get("args"), dict):
        return dict(call["args"])
    return {}


def _preview(result: Any) -> str:
    if isinstance(result, ToolMessage):
        return _text(result.content)[:RESULT_PREVIEW_CHARS]
    return str(result)[:RESULT_PREVIEW_CHARS]


def _text(content: Any) -> str:
    """Message content is a string, or a list of parts · flatten it.

    Gemini returns ``[{"type": "text", "text": "..."}]`` for ordinary replies.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "\n".join(parts)
    return str(content)
