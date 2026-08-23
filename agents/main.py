"""The one entry point every crew container runs.

There used to be six of these, one per role, and they were the same twenty-odd
lines with a different logger name. That sameness is the point worth making:
an Engineering Manager and a QA Engineer are the *same harness* holding a
different prompt and a different tool catalogue. Six copies obscured it.

The container's role arrives in its environment, set by the Job that launched
it. Everything else follows from that.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from contracts.agent_env import AgentIdentity

from agents.shared.agent_loop import run_agent
from agents.shared.logging_setup import setup_logging

log = setup_logging("agent-main")

PROMPT_DIRS: dict[str, str] = {
    "engineering_manager": "em",
    "product_manager": "pm",
    "backend_engineer": "backend",
    "frontend_engineer": "frontend",
    "qa_engineer": "qa",
    "code_reviewer": "reviewer",
}


def load_system_prompt(role: str) -> str:
    """Read the role's prompt from disk.

    A missing prompt fails here, at boot, rather than producing an agent with
    no instructions that answers plausibly and wrongly.
    """
    try:
        directory = PROMPT_DIRS[role]
    except KeyError:
        raise SystemExit(
            f"unknown role {role!r} · expected one of {sorted(PROMPT_DIRS)}"
        ) from None

    path = Path(__file__).parent / directory / "system_prompt.md"
    if not path.exists():
        raise SystemExit(f"no system prompt for {role} at {path}")
    return path.read_text()


async def main() -> None:
    identity = AgentIdentity.from_env()
    log.info(
        "container.starting role=%s project_id=%s agent=%s",
        identity.role,
        identity.project_id,
        identity.name,
    )
    await run_agent(load_system_prompt(identity.role))


if __name__ == "__main__":
    asyncio.run(main())
