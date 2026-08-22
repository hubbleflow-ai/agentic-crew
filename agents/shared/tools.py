"""MCP tool wrappers + Anthropic-compatible JSON schemas.

Each tool is:
  1. A async Python function that calls the corresponding MCP server via httpx
  2. A JSON schema describing its inputs (so Claude can call it)

Per-role tool catalogues live at the bottom of this file · each role gets
a subset matching its system prompt's allowed tools.

The Anthropic SDK tool-use loop calls the function by name with the
input dict; we dispatch via TOOL_FUNCTIONS.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Awaitable, Callable

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


async def _get(url: str) -> dict:
    resp = await _client.get(url)
    resp.raise_for_status()
    return resp.json()


# ─── ticket tools (source of truth) ──────────────────────────────────────

async def read_ticket(ticket_id: str) -> dict:
    return await _get(f"{MCP['tickets']}/tickets/{ticket_id}")


async def write_ticket(
    task_id: str,
    title: str,
    api_contract: str = "",
    acceptance_criteria: list[str] | None = None,
    assignments: dict[str, str] | None = None,
    evidence_cited: list[str] | None = None,
    status: str = "draft",
) -> dict:
    return await _post(f"{MCP['tickets']}/tickets", {
        "task_id": task_id,
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

async def sandbox_write_file(task_id: str, path: str, content: str) -> dict:
    return await _post(f"{MCP['sandbox']}/sandbox/write", {
        "task_id": task_id,
        "path": path,
        "content": content,
    })


async def sandbox_read_file(task_id: str, path: str) -> dict:
    return await _post(f"{MCP['sandbox']}/sandbox/read", {
        "task_id": task_id,
        "path": path,
    })


async def sandbox_exec(task_id: str, command: str, cwd: str = "/workspace") -> dict:
    return await _post(f"{MCP['sandbox']}/sandbox/exec", {
        "task_id": task_id,
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

async def spawn_agent(
    task_id: str,
    role: str,
    assignment: str = "",
    override_cap: bool = False,
) -> dict:
    """Spawn a teammate via the control plane (which enforces caps).

    If `assignment` is given, it is delivered to the new agent as a
    race-free bootstrap `work_assigned` message · the agent processes it
    the instant it boots, so there's no pub/sub timing gap.
    """
    cp = os.environ.get("CONTROL_PLANE_URL", "http://control-plane:8001")
    bootstrap = None
    if assignment:
        bootstrap = json.dumps({
            "from": "engineering_manager",
            "type": "work_assigned",
            "payload": {"role": role, "assignment": assignment, "task_id": task_id},
        })
    return await _post(f"{cp}/agents/spawn", {
        "task_id": task_id,
        "role": role,
        "requested_by": "engineering_manager",
        "override_cap": override_cap,
        "bootstrap_message": bootstrap,
    })


async def escalate_to_founder(
    task_id: str,
    question: str,
    context: str = "",
    options: list[str] | None = None,
) -> dict:
    """Raise a HITL gate to the Founder. Publishes an escalation event on
    the task channel (UI/CLI renders it) and returns an acknowledgement so
    the crew is never hard-blocked in headless runs."""
    cp = os.environ.get("CONTROL_PLANE_URL", "http://control-plane:8001")
    return await _post(f"{cp}/escalations", {
        "task_id": task_id,
        "question": question,
        "context": context,
        "options": options or [],
    })


# ─── JSON SCHEMAS for Anthropic tool definitions ─────────────────────────

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_ticket": {
        "name": "read_ticket",
        "description": (
            "Read a Task Ticket. The Task Ticket is the source of truth for "
            "this task · contains the API contract, acceptance criteria, and "
            "assignments. ALL agents should read this before doing any work."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "The ticket ID like TICKET-2031"},
            },
            "required": ["ticket_id"],
        },
    },
    "write_ticket": {
        "name": "write_ticket",
        "description": (
            "Write or update the Task Ticket · only the Engineering Manager "
            "should call this. Includes API contract, acceptance criteria, "
            "assignments to each role, and evidence citations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "title": {"type": "string"},
                "api_contract": {"type": "string", "description": "Markdown API spec"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "assignments": {
                    "type": "object",
                    "description": "Map of role name → assignment description",
                    "additionalProperties": {"type": "string"},
                },
                "evidence_cited": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of screenshot IDs cited as evidence",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "spec_complete", "in_progress", "done"],
                },
            },
            "required": ["task_id", "title"],
        },
    },
    "add_ticket_comment": {
        "name": "add_ticket_comment",
        "description": "Add a comment to a Task Ticket. Use to surface progress or questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string"},
                "author": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["ticket_id", "author", "body"],
        },
    },
    "navigate": {
        "name": "navigate",
        "description": (
            "Open a URL in the browser and return its page content. Use for "
            "research · compliance, market patterns, external service limits."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL including https://"},
            },
            "required": ["url"],
        },
    },
    "screenshot_and_annotate": {
        "name": "screenshot_and_annotate",
        "description": (
            "Navigate to a URL, take a screenshot, and annotate with your "
            "finding. The screenshot is saved as evidence and can be cited "
            "in tickets. Use this when you find something worth citing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "looking_for": {"type": "string", "description": "What you were researching"},
                "finding": {"type": "string", "description": "What you found, briefly"},
                "cite_as": {"type": "string", "description": "Optional short citation handle"},
            },
            "required": ["url", "looking_for", "finding"],
        },
    },
    "sandbox_write_file": {
        "name": "sandbox_write_file",
        "description": (
            "Write a file to the task's shared sandbox. Real Python file in "
            "a real container · subsequent sandbox_exec calls can run it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to /workspace"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "sandbox_read_file": {
        "name": "sandbox_read_file",
        "description": "Read a file from the task's shared sandbox.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    "sandbox_exec": {
        "name": "sandbox_exec",
        "description": (
            "Execute a shell command in the task's shared sandbox. Returns "
            "exit_code, stdout, stderr, wall_clock_ms. Use for pytest, ruff, "
            "mypy, bandit, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string", "default": "/workspace"},
            },
            "required": ["command"],
        },
    },
    "github_open_pr": {
        "name": "github_open_pr",
        "description": "Open a pull request on a branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["branch", "title"],
        },
    },
    "github_read_pr": {
        "name": "github_read_pr",
        "description": "Read a PR · returns diff, status, comments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer"},
            },
            "required": ["pr_number"],
        },
    },
    "github_post_comment": {
        "name": "github_post_comment",
        "description": "Post an inline review comment on a PR. Reference path + line for actionability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer"},
                "line": {"type": "integer"},
                "path": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["pr_number", "body"],
        },
    },
    "github_approve": {
        "name": "github_approve",
        "description": "Approve a PR. Reviewer only. Use sparingly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer"},
            },
            "required": ["pr_number"],
        },
    },
    "github_request_changes": {
        "name": "github_request_changes",
        "description": "Request changes on a PR. Reviewer's default action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer"},
                "summary": {"type": "string", "description": "Short summary of required changes"},
            },
            "required": ["pr_number"],
        },
    },
    "jira_create_issue": {
        "name": "jira_create_issue",
        "description": (
            "Create a follow-up JIRA issue (tech-debt, scope cut, future work). "
            "Use to file work that's NOT in the current task's scope."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "enum": ["TECH", "FEAT", "BUG"]},
                "type": {"type": "string", "enum": ["Task", "Story", "Bug", "Epic"]},
                "summary": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "linked_to": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary"],
        },
    },
    "spawn_agent": {
        "name": "spawn_agent",
        "description": (
            "Spawn a teammate to join this task. The control plane enforces "
            "per-role spawn caps. Pass `assignment` to hand the new agent its "
            "first piece of work · they pick it up the moment they boot. "
            "Roles: product_manager, backend_engineer, frontend_engineer, "
            "qa_engineer, code_reviewer. Set override_cap=true ONLY after the "
            "Founder has approved exceeding a cap."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": [
                        "product_manager", "backend_engineer", "frontend_engineer",
                        "qa_engineer", "code_reviewer",
                    ],
                },
                "assignment": {
                    "type": "string",
                    "description": "What this agent should do · their bootstrap task.",
                },
                "override_cap": {"type": "boolean", "default": False},
            },
            "required": ["role"],
        },
    },
    "escalate_to_founder": {
        "name": "escalate_to_founder",
        "description": (
            "Raise a Human-in-the-Loop gate to the Founder for a decision you "
            "cannot make alone (PII, security, compliance, scope, spend). "
            "Returns an acknowledgement · proceed with your best judgement if "
            "no human is online."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The decision needed, in one line."},
                "context": {"type": "string", "description": "Why this needs a human."},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The discrete choices the Founder can pick from.",
                },
            },
            "required": ["question"],
        },
    },
}


# ─── tool dispatcher · used by the agent loop ────────────────────────────

# Map tool name → async function. Functions take a kwargs dict.
TOOL_FUNCTIONS: dict[str, Callable[..., Awaitable[Any]]] = {
    "read_ticket": read_ticket,
    "write_ticket": write_ticket,
    "add_ticket_comment": add_ticket_comment,
    "navigate": navigate,
    "screenshot_and_annotate": screenshot_and_annotate,
    "sandbox_write_file": sandbox_write_file,
    "sandbox_read_file": sandbox_read_file,
    "sandbox_exec": sandbox_exec,
    "github_open_pr": github_open_pr,
    "github_read_pr": github_read_pr,
    "github_post_comment": github_post_comment,
    "github_approve": github_approve,
    "github_request_changes": github_request_changes,
    "jira_create_issue": jira_create_issue,
    "spawn_agent": spawn_agent,
    "escalate_to_founder": escalate_to_founder,
}


async def dispatch(tool_name: str, tool_input: dict, agent_id: str, task_id: str) -> dict:
    """Call a tool by name. agent_id and task_id are auto-injected where needed."""
    fn = TOOL_FUNCTIONS.get(tool_name)
    if not fn:
        return {"error": f"unknown tool: {tool_name}"}

    # Auto-inject agent_id and task_id for tools that need them
    args = dict(tool_input)
    if tool_name in ("navigate", "screenshot_and_annotate"):
        args["agent_id"] = agent_id
    elif tool_name in (
        "sandbox_write_file", "sandbox_read_file", "sandbox_exec",
        "spawn_agent", "escalate_to_founder",
    ):
        args["task_id"] = task_id

    try:
        return await fn(**args)
    except Exception as e:
        log.exception("tool.failed name=%s", tool_name)
        return {"error": str(e)}


# ─── per-role tool catalogues ────────────────────────────────────────────
# Each role sees ONLY the tools matching their system prompt's permissions.

ROLE_TOOLS: dict[str, list[str]] = {
    "engineering_manager": [
        "read_ticket", "write_ticket", "add_ticket_comment",
        "jira_create_issue",
        "github_read_pr",
        "spawn_agent", "escalate_to_founder",
    ],
    "product_manager": [
        "read_ticket", "add_ticket_comment",
        "navigate", "screenshot_and_annotate",
        "jira_create_issue",
    ],
    "backend_engineer": [
        "read_ticket",
        "sandbox_write_file", "sandbox_read_file", "sandbox_exec",
        "github_open_pr",
    ],
    "frontend_engineer": [
        "read_ticket",
        "sandbox_write_file", "sandbox_read_file", "sandbox_exec",
        "github_open_pr",
    ],
    "qa_engineer": [
        "read_ticket", "add_ticket_comment",
        "sandbox_read_file", "sandbox_exec",
        "github_read_pr",
        "navigate", "screenshot_and_annotate",  # real browser E2E checks
    ],
    "code_reviewer": [
        "read_ticket",
        "github_read_pr", "github_post_comment", "github_approve", "github_request_changes",
        "sandbox_exec",  # for running linters in own sandbox
    ],
}


def get_schemas_for_role(role: str) -> list[dict]:
    """Return the JSON tool schemas for a role · ready to pass to Anthropic SDK."""
    tool_names = ROLE_TOOLS.get(role, [])
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]
