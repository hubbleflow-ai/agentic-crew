"""Structured logging for every crew process.

Two sinks, deliberately:

  * **stdout** as JSON — so ``docker logs`` still works and a human can read a
    single container without any infrastructure running.
  * **Loki** over HTTP — so Grafana can query across every container at once.

There is no log shipper and no ``docker.sock`` mount. Each process pushes its
own lines, which keeps the security surface small and means logs behave the
same whether the process runs in Docker, in Kubernetes, or on a laptop.

Every record carries the same label set, so a Grafana query can slice by any of
them::

    {service="control-plane"}                   everything from one service
    {project_id="proj-1a2b"}                    one project, all of its agents
    {role="backend_engineer", level="ERROR"}    every backend failure

Context (project, task, agent, role) is bound once at start-up via
:func:`bind_context` and then attached to every subsequent record, so callers
never thread it through by hand.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any

_CONTEXT: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "log_context", default={}
)

# Fields the stdlib puts on every record. Anything else the caller passed via
# `extra=` is ours, and belongs in the JSON payload.
_STDLIB_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"asctime", "message", "taskName"}


def bind_context(**fields: str) -> None:
    """Attach fields to every record emitted from here on.

    Call once, at process start::

        bind_context(service="backend_engineer", agent_id=..., task_id=...)
    """
    merged = {**_CONTEXT.get(), **{k: str(v) for k, v in fields.items() if v}}
    _CONTEXT.set(merged)


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_CONTEXT.get(),
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in _STDLIB_ATTRS}
        if extras:
            payload.update(extras)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class LokiHandler(logging.Handler):
    """Ship records to Loki from a background thread.

    Logging must never block or break the thing being logged, so this drops
    records when the queue is full and swallows transport errors. A missing
    Loki is a degraded dashboard, not a failed agent.
    """

    # Loki labels must stay low-cardinality; the message text goes in the line,
    # never in a label.
    _LABEL_KEYS = ("service", "role", "project_id", "level")

    def __init__(
        self, url: str, *, max_queue: int = 2000, flush_seconds: float = 1.0
    ) -> None:
        super().__init__()
        self._url = url.rstrip("/") + "/loki/api/v1/push"
        self._queue: queue.Queue[tuple[dict[str, str], str, str]] = queue.Queue(max_queue)
        self._flush_seconds = flush_seconds
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="loki-shipper", daemon=True
        )
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        # Logging must never raise into the caller.
        with contextlib.suppress(Exception):
            line = self.format(record)
            ctx = {**_CONTEXT.get(), "level": record.levelname}
            labels = {k: ctx[k] for k in self._LABEL_KEYS if ctx.get(k)}
            labels.setdefault("service", record.name)
            self._queue.put_nowait((labels, str(time.time_ns()), line))

    def _run(self) -> None:
        while not self._stopping.is_set():
            time.sleep(self._flush_seconds)
            self._drain()

    def _drain(self) -> None:
        batch: list[tuple[dict[str, str], str, str]] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return
        streams: dict[str, dict[str, Any]] = {}
        for labels, ts, line in batch:
            key = json.dumps(labels, sort_keys=True)
            streams.setdefault(key, {"stream": labels, "values": []})["values"].append(
                [ts, line]
            )
        body = json.dumps({"streams": list(streams.values())}).encode()
        request = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}
        )
        # Loki being down is a degraded dashboard, not a failed agent —
        # stdout still has every line.
        with contextlib.suppress(urllib.error.URLError, OSError):
            urllib.request.urlopen(request, timeout=5).close()

    def close(self) -> None:
        self._stopping.set()
        self._drain()
        super().close()


def setup_logging(service: str, **context: str) -> logging.Logger:
    """Configure root logging for a process and return its logger.

    Reads ``LOG_LEVEL`` (default ``INFO``) and ``LOKI_URL``. Without
    ``LOKI_URL`` only the stdout sink is installed, so a laptop run needs no
    infrastructure at all.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    stdout = logging.StreamHandler()
    stdout.setFormatter(JsonFormatter())
    root.addHandler(stdout)

    loki_url = os.environ.get("LOKI_URL", "").strip()
    if loki_url:
        loki = LokiHandler(loki_url)
        loki.setFormatter(JsonFormatter())
        root.addHandler(loki)

    # These are chatty at INFO and drown the crew's own lines.
    for noisy in ("httpx", "httpcore", "urllib3", "docker", "openinference"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    bind_context(service=service, **context)
    return logging.getLogger(service)
