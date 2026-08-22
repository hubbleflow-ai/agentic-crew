"""Wiring · the one place that decides which adapter satisfies which port.

Everything else in the application asks for a port. This module is where the
real Kubernetes runtime and the real Redis bus are chosen, and it is the only
file a test has to bypass to run the whole service against fakes.
"""

from __future__ import annotations

import os

from apps.control_plane.service import CrewService
from fastapi import Request


def get_service(request: Request) -> CrewService:
    """Hand the request the service built at startup."""
    service: CrewService = request.app.state.service
    return service


def settings() -> dict[str, str]:
    """Configuration, read once at startup.

    Everything here arrives from the cluster's ConfigMap and Secret · the
    control plane holds no hostnames or keys of its own.
    """
    return {
        "namespace": os.environ.get("CREW_NAMESPACE", "crew"),
        "agent_image": os.environ.get("CREW_AGENT_IMAGE", "crew-agent:dev"),
        "redis_url": os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    }
