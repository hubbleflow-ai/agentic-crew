"""Backend Engineer · entry point."""

from __future__ import annotations

import asyncio
import logging

from agents.shared.agent_loop import AgentContext, load_system_prompt, run_agent_loop

logging.basicConfig(level="INFO", format="[%(name)s] %(message)s")

log = logging.getLogger("backend")


async def main() -> None:
    ctx = AgentContext()
    system_prompt = load_system_prompt(__file__)
    log.info("backend.starting agent_id=%s task=%s", ctx.agent_id, ctx.task_id)
    await run_agent_loop(ctx, system_prompt)


if __name__ == "__main__":
    asyncio.run(main())
