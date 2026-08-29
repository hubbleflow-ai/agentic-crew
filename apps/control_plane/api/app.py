"""The FastAPI application · assembly only.

Every concrete choice is made here and nowhere else: Kubernetes for the
runtime, Redis for the bus, Redis for the store. Swapping any of them is a
line in :func:`lifespan`, because everything downstream depends on a port.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from agents.shared.logging_setup import setup_logging
from apps.control_plane.adapters.k8s_runtime import KubernetesAgentRuntime
from apps.control_plane.adapters.redis_events import RedisEventBus
from apps.control_plane.adapters.redis_store import RedisProjectStore
from apps.control_plane.api.deps import settings
from apps.control_plane.api.routes import router
from apps.control_plane.service import CrewService
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

log = setup_logging("control-plane")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the adapters, hand them to the service, take them down again."""
    config = settings()

    runtime = KubernetesAgentRuntime(
        namespace=config["namespace"], image=config["agent_image"]
    )
    await runtime.connect()
    events = RedisEventBus(config["redis_url"])
    # The same Redis carries the live bus and the durable state. One
    # dependency, two roles · a restarted control plane rejoins projects it
    # opened before rather than starting the founder's day again.
    store = RedisProjectStore(config["redis_url"])

    service = CrewService(store=store, runtime=runtime, events=events)
    app.state.service = service

    # One subscriber writing down everything every agent says. Without it the
    # only recorded history is what this process itself emitted.
    #
    # Two things here are not decoration. `asyncio` holds only a *weak*
    # reference to a task, so one kept alive by a local variable alone can be
    # collected mid-flight; it is parked on `app.state` instead. And a task
    # nobody awaits swallows its own exception -- this recorder died silently
    # at startup once, and the only symptom was that every project's history
    # was empty, days later. The callback makes that impossible to miss.
    recorder = asyncio.create_task(service.record(), name="recorder")
    app.state.recorder = recorder

    def _recorder_stopped(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("recorder.died · project history stops here", exc_info=error)
        else:
            log.error("recorder.exited · it should run for the life of the process")

    recorder.add_done_callback(_recorder_stopped)
    log.info("recorder.started pattern=all-projects")
    log.info(
        "control-plane.ready namespace=%s image=%s",
        config["namespace"],
        config["agent_image"],
    )

    try:
        yield
    finally:
        recorder.cancel()
        with suppress(asyncio.CancelledError):
            await recorder
        await runtime.aclose()
        await events.aclose()
        await store.aclose()
        log.info("control-plane.stopped")


def create_app() -> FastAPI:
    """Build the app · a function, not a module-level global, so a test can
    build one with fake adapters without importing a live Kubernetes client."""
    app = FastAPI(
        title="Agentic Crew · control plane",
        version="0.3.0",
        summary="Opens projects, launches agents as Kubernetes Jobs, and streams what they do.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # a laptop demo · tighten before this leaves one
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    # Run the control plane under a debugger without uvicorn's CLI.
    import uvicorn

    uvicorn.run("apps.control_plane.api.app:app", host="0.0.0.0", port=8000, reload=True)
