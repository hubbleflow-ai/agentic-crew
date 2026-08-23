# The image every Python service in the crew runs: control plane, agents, and
# the MCP mocks that do not need a browser.
#
# Two things it deliberately does NOT contain:
#
#   * the Docker CLI. Agents are Kubernetes Jobs now, created through the API
#     server by a ServiceAccount with a Role that permits exactly that. Nothing
#     needs a socket to the host daemon, and mounting one would hand every
#     container the keys to the machine.
#   * Chromium. It is ~1GB, and only mcp-browser uses it. That image extends
#     this one · see browser.Dockerfile.

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

# ripgrep above is not incidental · the harness's search tool falls back to a
# Python implementation without it, and logs a line saying so on every boot.

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY contracts /app/contracts
COPY apps /app/apps
COPY agents /app/agents
COPY mocks /app/mocks

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Runs as a normal user · the Job's securityContext also demands non-root, and
# an image that only works as root would fail admission rather than silently
# escalate.
RUN useradd --uid 1000 --create-home crew && chown -R crew:crew /app
USER crew

CMD ["python", "-m", "agents.main"]
