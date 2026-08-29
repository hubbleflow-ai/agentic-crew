"""Per-role tool catalogues.

DeepAgents builds a tool's schema from a plain function's name, signature and
docstring. The functions in :mod:`agents.shared.tools` take infrastructure
arguments — which project, which agent — that a model has no way to know, so
each is wrapped here in a closure that binds them and exposes only the
parameters worth asking a model for.

Every role also gets the harness's built-in filesystem tools for free, rooted
at the project's volume. These catalogues cover what the built-ins do not: the
ticket, the sandbox, GitHub, browser research, naming the project, spawning
teammates, and escalation.

The catalogue *is* the authority on what a role can do. A backend engineer has
no ``spawn_agent`` here, so no prompt wording can talk it into building itself
a team.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import httpx
from contracts.agent_env import AgentIdentity

from agents.shared import tools as T
from agents.shared.logging_setup import setup_logging

log = setup_logging("agent-tools")


def _safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool coroutine so it NEVER raises · a failing MCP/network call
    returns an error payload instead of aborting the whole agent turn (LangGraph
    re-raises tool errors by default). functools.wraps preserves the signature
    + docstring so LangChain still derives the right tool schema."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            log.warning("tool.failed name=%s err=%s", getattr(fn, "__name__", "?"), e)
            return {"error": f"{getattr(fn, '__name__', 'tool')} failed: {e}"}

    return wrapper


# ─── per-role inclusion map ──────────────────────────────────────────────

ROLE_TOOLS: dict[str, list[str]] = {
    "engineering_manager": [
        "read_ticket", "write_ticket", "add_ticket_comment",
        "jira_create_issue", "spawn_agent", "escalate_to_founder",
        # Only the EM names the project · it is the role that scopes the work.
        "name_project",
        # Founder messages arrive addressed to the EM. This is how it hands one
        # to the PM instead of answering a product question itself.
        "delegate_to",
    ],
    # No browser and no jira · the PM writes the spec from what it knows, in
    # one pass. Research was the loop: every scope claim demanded a source,
    # every source demanded a screenshot.
    "product_manager": [
        "read_ticket", "add_ticket_comment",
    ],
    "backend_engineer": [
        "read_ticket", "sandbox_exec", "github_open_pr",
    ],
    "frontend_engineer": [
        "read_ticket", "sandbox_exec", "github_open_pr",
    ],
    # One pass over the code that exists · running it is the check, so no
    # browser and no PR reading.
    "qa_engineer": [
        "read_ticket", "add_ticket_comment", "sandbox_exec",
    ],
    "code_reviewer": [
        "read_ticket", "github_read_pr", "github_post_comment",
        "github_approve", "github_request_changes", "sandbox_exec",
    ],
}


def build_role_tools(identity: AgentIdentity) -> list[Callable[..., Any]]:
    """The bound tools this role is allowed to call."""
    project_id = identity.project_id
    agent_id = identity.name
    author = identity.role
    # One ticket per project, with an id derived the same way the ticket store
    # derives it. Bound here so a model never has to guess one · guessing
    # produced a steady trickle of 404s.
    ticket_id = f"TICKET-{project_id.split('-')[-1].upper()}"

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
            project_id=project_id, title=title, api_contract=api_contract,
            acceptance_criteria=acceptance_criteria, assignments=assignments,
            status=status,
        )

    async def add_ticket_comment(body: str) -> dict:
        """Add a comment to THIS task's Task Ticket (surface progress or a question)."""
        return await T.add_ticket_comment(ticket_id=ticket_id, author=author, body=body)

    async def sandbox_exec(command: str, cwd: str = "/workspace") -> dict:
        """Run a shell command (pytest, ruff, mypy, bandit, ...) in this
        project's sandbox. Returns exit_code, stdout, stderr."""
        return await T.sandbox_exec(project_id=project_id, command=command, cwd=cwd)

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

    async def name_project(name: str) -> dict:
        """Give this project its real name, replacing "New Project".

        Call this ONCE, as soon as you know what is actually being built —
        the founder's chat is titled with it. Name it after the work ("Rate
        limiting for the public API"), not the conversation."""
        return await T.name_project(project_id=project_id, name=name)

    async def delegate_to(role: str, message: str) -> dict:
        """Hand a question or a piece of work to a teammate already on this
        project, by role. Use this when the founder asks something the Product
        Manager should answer rather than you — you receive founder messages
        first and decide who takes them.

        Returns not_present if nobody holds that role yet; spawn one instead."""
        result = await T.delegate(
            project_id=project_id, sender=f"{author}/{agent_id}",
            to_role=role, text=message,
        )
        if not result.get("delivered"):
            return {
                "status": "not_present",
                "message": (
                    f"No {role} is on this project yet. Spawn one with "
                    f"spawn_agent and put this in their assignment."
                ),
            }
        return result

    async def spawn_agent(role: str, assignment: str = "") -> dict:
        """Add a teammate to this project. role is one of: product_manager,
        backend_engineer, frontend_engineer, qa_engineer, code_reviewer.

        `assignment` is their first piece of work — they have it the moment
        they boot. Spawn caps are enforced by the control plane; a refusal
        tells you which limit was hit and what to do instead."""
        try:
            return await T.spawn_agent(
                project_id=project_id, role=role, assignment=assignment
            )
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 409:
                # Hand the refusal to the model in its own words · a bare
                # error invites a retry loop against a limit that will not
                # move.
                detail = e.response.json().get("detail", {})
                return {
                    "status": "refused",
                    "scope": detail.get("scope", "project"),
                    "message": detail.get("message", f"cap reached for {role}"),
                }
            raise

    async def escalate_to_founder(
        question: str, context: str = "", options: list[str] | None = None,
    ) -> dict:
        """Raise a Human-in-the-Loop decision to the Founder (PII, security,
        compliance, scope, spend). Returns an acknowledgement."""
        return await T.escalate_to_founder(
            project_id=project_id, question=question, context=context, options=options,
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
        "name_project": name_project,
        "delegate_to": delegate_to,
        "spawn_agent": spawn_agent,
        "escalate_to_founder": escalate_to_founder,
    }
    # Make every tool error-safe so a flaky MCP/network call can't abort the
    # agent's turn · it just returns an error payload the LLM can react to.
    catalog = {name: _safe(fn) for name, fn in catalog.items()}

    names = ROLE_TOOLS.get(identity.role, [])
    return [catalog[n] for n in names if n in catalog]
