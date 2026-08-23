"""Phoenix tracing · every Gemini / LangGraph call + HTTP hop becomes a span.

Ported from the Session 6 multi-agent project. Call `setup_observability(role)`
once at the start of each agent process. When `PHOENIX_COLLECTOR_ENDPOINT` is
set (compose points it at the phoenix service), this:

  - auto-instruments LangChain/LangGraph (openinference) · the DeepAgents agent
    loop + every Gemini call emits spans;
  - auto-instruments outbound httpx · the MCP tool calls (tickets, sandbox,
    browser, github, control-plane) become child spans, and the W3C
    `traceparent` header is propagated across services;
  - tags every span with `service.name = crew/<role>` so you can filter by
    agent in the Phoenix UI (http://localhost:6006).

If the collector env is unset, this is a silent no-op (no tracing, no errors).
Never takes a service down · all failures are logged and swallowed.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_initialized: set[str] = set()

PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "agentic-crew")


def setup_observability(service_name: str) -> None:
    """Wire the Phoenix span collector + auto-instrument LangChain / httpx.
    Idempotent per service_name."""
    if service_name in _initialized:
        return
    _initialized.add(service_name)

    collector = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", "").rstrip("/")
    if not collector:
        log.info("observability.no_collector service=%s · tracing off", service_name)
        return

    # Optional LangSmith dual-sink · only if a key is present.
    if os.environ.get("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", PROJECT_NAME)

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from phoenix.otel import register

        tracer_provider = register(
            project_name=PROJECT_NAME,
            endpoint=f"{collector}/v1/traces",
            resource=Resource.create({"service.name": f"crew/{service_name}"}),
            set_global_tracer_provider=True,
            # Batch, never simple. A SimpleSpanProcessor exports on the
            # calling thread, so when the collector is unreachable every span
            # blocks for the full retry ladder -- roughly seven seconds each.
            # An agent traced that way does not fail; it just stops, which is
            # far harder to diagnose than a crash.
            batch=True,
        )
        # The exporter logs a warning per failed attempt. When the collector
        # is down that is dozens a second, and it buries everything the agent
        # says. One line at ERROR is enough to notice.
        logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.ERROR)

        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)
        log.info(
            "observability.initialized service=%s project=%s collector=%s",
            service_name, PROJECT_NAME, collector,
        )
    except Exception as e:
        log.exception(
            "observability.init_failed service=%s err=%s · continuing without tracing",
            service_name, e,
        )
