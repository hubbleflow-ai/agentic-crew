# Shared image for all Python services: control plane, mocks, and agents.
# Each role specifies its entrypoint at runtime via `command:` in compose
# or via the control plane's `docker run` args when spawning agents.
#
# Includes:
#   - Python 3.12 + Claude Agent SDK + FastAPI
#   - Docker CLI (control plane + mcp_sandbox both spawn sibling containers)
#   - Playwright + Chromium (mcp_browser uses real headless Chromium)
#
# The image is heavier than ideal (~1.5GB) because of Playwright's Chromium.
# v1.1 will split into a base image + browser-needing image to slim down.

FROM python:3.12-slim

WORKDIR /app

# System deps · docker CLI + Playwright browser deps
# Playwright's `--with-deps chromium` pulls in all the system libs Chromium
# needs (X11, fonts, audio, etc). Without --with-deps the browser won't start.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Python deps · single requirements.txt for the whole stack
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser binaries
# Two-step: install playwright via pip (done above), then download Chromium.
RUN playwright install --with-deps chromium

# Application code
COPY apps /app/apps
COPY agents /app/agents
COPY mocks /app/mocks

# Default Python path so `from apps.x import y` works
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Default command overridden by compose `command:` or `docker run`.
CMD ["python", "-c", "print('crew-agent: no command specified · override via compose or docker run')"]
