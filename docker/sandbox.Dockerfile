# Sandbox image · what mcp_sandbox spawns per-task.
# Each task gets ONE sandbox container that all of that task's agents
# share. Backend writes files there, runs pytest, etc.
#
# Pre-installed: pytest, ruff, mypy, bandit · so first command doesn't
# pay the install cost. Add more as needed.

FROM python:3.12-slim

# Common Python testing + linting tools agents may use
RUN pip install --no-cache-dir \
    pytest>=8.0.0 \
    pytest-asyncio>=0.24.0 \
    ruff>=0.6.0 \
    mypy>=1.11.0 \
    bandit>=1.7.0 \
    httpx>=0.27.0 \
    fastapi>=0.115.0 \
    sqlalchemy>=2.0.30 \
    pydantic>=2.9.0

WORKDIR /workspace

# The sandbox stays alive via tail -f. mcp_sandbox uses `docker exec` to
# run commands in this container.
CMD ["tail", "-f", "/dev/null"]
