"""Per-agent DeepAgents tool catalogues.

DeepAgents/LangChain build a tool's schema from a plain function's name,
signature, and docstring. Our `tools.py` functions take infra args like
`task_id` / `agent_id` that the LLM shouldn't (and can't) supply — so here we
wrap each one in a closure that binds those from the AgentContext and exposes
only the meaningful, LLM-facing parameters.

Engineers ALSO get DeepAgents' built-in filesystem tools (write_file /
read_file / edit / ls) for free, rooted at the persistent `/workspace/<task>`
volume — so these custom tools focus on the things the built-ins don't cover:
the ticket, running tests in the sandbox, GitHub, browser research, spawning
teammates, and escalation.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

import httpx

from agents.shared import tools as T

log = logging.getLogger(__name__)


def _safe(fn):
    """Wrap a tool coroutine so it NEVER raises · a failing MCP/network call
    returns an error payload instead of aborting the whole agent turn (LangGraph
    re-raises tool errors by default). functools.wraps preserves the signature
    + docstring so LangChain still derives the right tool schema."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - deliberately broad
            log.warning("tool.failed name=%s err=%s", getattr(fn, "__name__", "?"), e)
            return {"error": f"{getattr(fn, '__name__', 'tool')} failed: {e}"}

    return wrapper


# ─── per-role inclusion map ──────────────────────────────────────────────

ROLE_TOOLS: dict[str, list[str]] = {
    "engineering_manager": [
        "read_ticket", "write_ticket", "add_ticket_comment",
        "jira_create_issue", "spawn_agent", "escalate_to_founder",
    ],
    "product_manager": [
        "read_ticket", "add_ticket_comment",
        "navigate", "screenshot_and_annotate", "jira_create_issue",
    ],
    "backend_engineer": [
        "read_ticket", "sandbox_exec", "github_open_pr",
    ],
    "frontend_engineer": [
        "read_ticket", "sandbox_exec", "github_open_pr",
    ],
    "qa_engineer": [
        "read_ticket", "add_ticket_comment", "sandbox_exec",
        "github_read_pr", "navigate", "screenshot_and_annotate",
    ],
    "code_reviewer": [
        "read_ticket", "github_read_pr", "github_post_comment",
        "github_approve", "github_request_changes", "sandbox_exec",
    ],
}


def build_role_tools(ctx) -> list:
    """Return the list of bound DeepAgents tool functions for ctx.role."""
    task_id = ctx.task_id
    agent_id = ctx.agent_id
    author = ctx.role
    # There is exactly ONE ticket per task · its id is derived from the task id
    # the same way the ticket store derives it. Bind it so agents never have to
    # guess a ticket id (which caused 404s).
    ticket_id = f"TICKET-{task_id.split('-')[-1].upper()}"

    async def read_ticket() -> dict:
        """Read THIS task's Task Ticket — the source of truth (API contract,
        acceptance criteria, assignments). Read it before working. If the
        Engineering Manager hasn't authored it yet, returns a clean
        'no ticket yet' status (NOT an error)."""
        try:
            return await T.read_ticket(ticket_id)
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                return {
                    "status": "no_ticket",
                    "message": (
                        "No ticket has been authored for this task yet — the "
                        "Engineering Manager writes it after scope is approved. "
                        "Wait for it, or proceed from the assignment you were given."
                    ),
                }
            raise

    async def write_ticket(
        title: str,
        api_contract: str = "",
        acceptance_criteria: list[str] | None = None,
        assignments: dict[str, str] | None = None,
        status: str = "draft",
    ) -> dict:
        """Author or update the Task Ticket (Engineering Manager only). Include
        the API contract, acceptance criteria, and an assignment per role."""
        return await T.write_ticket(
            task_id=task_id, title=title, api_contract=api_contract,
            acceptance_criteria=acceptance_criteria, assignments=assignments,
            status=status,
        )

    async def add_ticket_comment(body: str) -> dict:
        """Add a comment to THIS task's Task Ticket (surface progress or a question)."""
        return await T.add_ticket_comment(ticket_id=ticket_id, author=author, body=body)

    async def sandbox_exec(command: str, cwd: str = "/workspace") -> dict:
        """Run a shell command (pytest, ruff, mypy, bandit, etc.) in the task's
        shared sandbox. Returns exit_code, stdout, stderr."""
        return await T.sandbox_exec(task_id=task_id, command=command, cwd=cwd)

    async def navigate(url: str) -> dict:
        """Open a URL in a real browser and return its page content — for
        research (compliance, market patterns, external service limits)."""
        return await T.navigate(agent_id=agent_id, url=url)

    async def screenshot_and_annotate(
        url: str, looking_for: str, finding: str, cite_as: str = "",
    ) -> dict:
        """Navigate to a URL, screenshot it, and record your finding as cited
        evidence that can be referenced in the ticket."""
        return await T.screenshot_and_annotate(
            agent_id=agent_id, url=url, looking_for=looking_for,
            finding=finding, cite_as=cite_as,
        )

    async def github_open_pr(branch: str, title: str, body: str = "") -> dict:
        """Open a pull request on a branch."""
        return await T.github_open_pr(branch=branch, title=title, body=body)

    async def github_read_pr(pr_number: int) -> dict:
        """Read a PR — returns its diff, status, and comments."""
        return await T.github_read_pr(pr_number)

    async def github_post_comment(
        pr_number: int, body: str, path: str = "", line: int = 0,
    ) -> dict:
        """Post an inline review comment on a PR (reference path + line)."""
        return await T.github_post_comment(
            pr_number=pr_number, author=author, line=line, path=path, body=body,
        )

    async def github_approve(pr_number: int) -> dict:
        """Approve a PR (Reviewer only). Use sparingly."""
        return await T.github_approve(pr_number=pr_number, reviewer=author)

    async def github_request_changes(pr_number: int, summary: str = "") -> dict:
        """Request changes on a PR — the Reviewer's default action."""
        return await T.github_request_changes(
            pr_number=pr_number, reviewer=author, summary=summary,
        )

    async def jira_create_issue(
        summary: str, description: str = "",
        project: str = "TECH", type: str = "Task", priority: str = "P2",
    ) -> dict:
        """File a follow-up JIRA issue for work NOT in the current task's scope
        (tech-debt, scope cuts, future work)."""
        return await T.jira_create_issue(
            project=project, type=type, summary=summary,
            description=description, priority=priority, reporter=author,
        )

    async def spawn_agent(role: str, assignment: str = "") -> dict:
        """Spawn a teammate container to join this task. role is one of:
        product_manager, backend_engineer, frontend_engineer, qa_engineer,
        code_reviewer. `assignment` is their first task — they pick it up the
        moment they boot. The control plane enforces per-role spawn caps; a
        409 means that role is already at capacity — don't retry."""
        try:
            return await T.spawn_agent(task_id=task_id, role=role, assignment=assignment)
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 409:
                return {
                    "status": "at_capacity",
                    "message": (
                        f"A {role} is already running for this task (spawn cap "
                        f"reached). You don't need another — work with the one "
                        f"you have. Do not retry this spawn."
                    ),
                }
            raise

    async def escalate_to_founder(
        question: str, context: str = "", options: list[str] | None = None,
    ) -> dict:
        """Raise a Human-in-the-Loop decision to the Founder (PII, security,
        compliance, scope, spend). Returns an acknowledgement."""
        return await T.escalate_to_founder(
            task_id=task_id, question=question, context=context, options=options,
        )

    catalog: dict[str, Any] = {
        "read_ticket": read_ticket,
        "write_ticket": write_ticket,
        "add_ticket_comment": add_ticket_comment,
        "sandbox_exec": sandbox_exec,
        "navigate": navigate,
        "screenshot_and_annotate": screenshot_and_annotate,
        "github_open_pr": github_open_pr,
        "github_read_pr": github_read_pr,
        "github_post_comment": github_post_comment,
        "github_approve": github_approve,
        "github_request_changes": github_request_changes,
        "jira_create_issue": jira_create_issue,
        "spawn_agent": spawn_agent,
        "escalate_to_founder": escalate_to_founder,
    }
    # Make every tool error-safe so a flaky MCP/network call can't abort the
    # agent's turn · it just returns an error payload the LLM can react to.
    catalog = {name: _safe(fn) for name, fn in catalog.items()}

    names = ROLE_TOOLS.get(ctx.role, [])
    return [catalog[n] for n in names if n in catalog]
