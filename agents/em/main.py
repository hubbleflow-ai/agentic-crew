"""Engineering Manager · entry point.

Thin wrapper · all reasoning happens in shared/agent_loop.py via Claude.
The system_prompt.md sitting next to this file defines EM behavior.
"""

from __future__ import annotations

import asyncio
import logging

from agents.shared.agent_loop import AgentContext, load_system_prompt, run_agent_loop

logging.basicConfig(level="INFO", format="[%(name)s] %(message)s")

log = logging.getLogger("em")


async def main() -> None:
    ctx = AgentContext()
    system_prompt = load_system_prompt(__file__)
    log.info("em.starting agent_id=%s task=%s", ctx.agent_id, ctx.task_id)
    await run_agent_loop(ctx, system_prompt)


if __name__ == "__main__":
    asyncio.run(main())
