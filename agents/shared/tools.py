"""HTTP wrappers around the crew's MCP services.

One plain async function per capability. That is the whole file · DeepAgents
builds each tool's schema from the function's name, signature and docstring,
so there is nothing to declare twice.

This file used to also carry hand-written JSON schemas and a name-based
dispatcher, written for the Anthropic SDK's tool-use loop before the harness
existed. 370 lines of it, describing the functions directly below it. The
harness derives all of it, so it is gone.

Binding of infrastructure arguments (which project, which agent) happens in
:mod:`agents.shared.agent_tools` · a model should never be asked to supply an
id it has no way of knowing.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)


# ─── MCP server URLs ─────────────────────────────────────────────────────

MCP = {
    "tickets": os.environ.get("MCP_TICKETS_URL", "http://mcp-tickets:9001"),
    "github":  os.environ.get("MCP_GITHUB_URL",  "http://mcp-github:9002"),
    "sandbox": os.environ.get("MCP_SANDBOX_URL", "http://mcp-sandbox:9003"),
    "browser": os.environ.get("MCP_BROWSER_URL", "http://mcp-browser:9004"),
    "jira":    os.environ.get("MCP_JIRA_URL",    "http://mcp-jira:9005"),
}

_client = httpx.AsyncClient(timeout=30.0)


async def _post(url: str, payload: dict) -> dict:
    resp = await _client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


async def _patch(url: str, payload: dict) -> dict:
    resp = await _client.patch(url, json=payload)
    resp.raise_for_status()
    return resp.json()


async def _get(url: str) -> dict:
    resp = await _client.get(url)
    resp.raise_for_status()
    return resp.json()


# ─── ticket tools (source of truth) ──────────────────────────────────────

async def read_ticket(ticket_id: str) -> dict:
    return await _get(f"{MCP['tickets']}/tickets/{ticket_id}")


async def write_ticket(
    project_id: str,
    title: str,
    api_contract: str = "",
    acceptance_criteria: list[str] | None = None,
    assignments: dict[str, str] | None = None,
    evidence_cited: list[str] | None = None,
    status: str = "draft",
) -> dict:
    return await _post(f"{MCP['tickets']}/tickets", {
        "project_id": project_id,
        "title": title,
        "api_contract": api_contract,
        "acceptance_criteria": acceptance_criteria or [],
        "assignments": assignments or {},
        "evidence_cited": evidence_cited or [],
        "status": status,
    })


async def add_ticket_comment(ticket_id: str, author: str, body: str) -> dict:
    return await _post(f"{MCP['tickets']}/tickets/{ticket_id}/comments", {
        "author": author,
        "body": body,
    })


# ─── browser tools (PM evidence trail) ───────────────────────────────────

async def navigate(agent_id: str, url: str) -> dict:
    return await _post(f"{MCP['browser']}/browser/navigate", {
        "agent_id": agent_id,
        "url": url,
    })


async def screenshot_and_annotate(
    agent_id: str,
    url: str,
    looking_for: str,
    finding: str,
    cite_as: str = "",
) -> dict:
    return await _post(f"{MCP['browser']}/browser/screenshot_and_annotate", {
        "agent_id": agent_id,
        "url": url,
        "looking_for": looking_for,
        "finding": finding,
        "cite_as": cite_as,
    })


# ─── sandbox tools (real CodeAct) ────────────────────────────────────────

async def sandbox_write_file(project_id: str, path: str, content: str) -> dict:
    return await _post(f"{MCP['sandbox']}/sandbox/write", {
        "project_id": project_id,
        "path": path,
        "content": content,
    })


async def sandbox_read_file(project_id: str, path: str) -> dict:
    return await _post(f"{MCP['sandbox']}/sandbox/read", {
        "project_id": project_id,
        "path": path,
    })


async def sandbox_exec(project_id: str, command: str, cwd: str = "/workspace") -> dict:
    return await _post(f"{MCP['sandbox']}/sandbox/exec", {
        "project_id": project_id,
        "command": command,
        "cwd": cwd,
    })


# ─── github tools (mocked, but same interface) ──────────────────────────

async def github_open_pr(branch: str, title: str, body: str = "") -> dict:
    return await _post(f"{MCP['github']}/github/open_pr", {
        "branch": branch,
        "title": title,
        "body": body,
    })


async def github_read_pr(pr_number: int) -> dict:
    return await _get(f"{MCP['github']}/github/pr/{pr_number}")


async def github_post_comment(pr_number: int, author: str, line: int, path: str, body: str) -> dict:
    return await _post(f"{MCP['github']}/github/post_comment", {
        "pr_number": pr_number,
        "author": author,
        "line": line,
        "path": path,
        "body": body,
    })


async def github_approve(pr_number: int, reviewer: str) -> dict:
    return await _post(f"{MCP['github']}/github/approve", {
        "pr_number": pr_number,
        "reviewer": reviewer,
    })


async def github_request_changes(pr_number: int, reviewer: str, summary: str = "") -> dict:
    return await _post(f"{MCP['github']}/github/request_changes", {
        "pr_number": pr_number,
        "reviewer": reviewer,
        "summary": summary,
    })


# ─── jira tools (follow-up tickets) ──────────────────────────────────────

async def jira_create_issue(
    project: str = "TECH",
    type: str = "Task",
    summary: str = "",
    description: str = "",
    priority: str = "P2",
    labels: list[str] | None = None,
    reporter: str = "",
    linked_to: list[str] | None = None,
) -> dict:
    return await _post(f"{MCP['jira']}/jira/issues", {
        "project": project,
        "type": type,
        "summary": summary,
        "description": description,
        "priority": priority,
        "labels": labels or [],
        "reporter": reporter,
        "linked_to": linked_to or [],
    })


# ─── orchestration tools (EM only) ───────────────────────────────────────

def _control_plane() -> str:
    return os.environ.get("CONTROL_PLANE_URL", "http://control-plane:8000")


async def spawn_agent(
    project_id: str,
    role: str,
    assignment: str = "",
    override_cap: bool = False,
) -> dict:
    """Ask the control plane for a teammate. It enforces the caps.

    The assignment travels in the request rather than as a follow-up message:
    the control plane injects it into the new container's environment, so the
    agent has its instructions the instant it boots and cannot miss a message
    published while it was still starting.
    """
    return await _post(
        f"{_control_plane()}/projects/{project_id}/agents",
        {"role": role, "assignment": assignment, "override": override_cap},
    )


async def name_project(project_id: str, name: str) -> dict:
    """Replace the placeholder chat title with a real one."""
    return await _patch(f"{_control_plane()}/projects/{project_id}", {"name": name})


async def escalate_to_founder(
    project_id: str,
    question: str,
    context: str = "",
    options: list[str] | None = None,
) -> dict:
    """Put a decision in front of the founder and carry on.

    Deliberately non-blocking · a headless run with no human watching must not
    deadlock a whole crew on a question nobody will answer.
    """
    return await _post(
        f"{_control_plane()}/projects/{project_id}/escalations",
        {"question": question, "context": context, "options": options or []},
    )
