"""mcp-jira · ticket tracker for tech-debt and follow-up work.

Distinct from mcp-tickets (which is the per-task source of truth). JIRA
holds longer-lived issues · scope cuts, tech debt, future enhancements,
bugs filed during the task.

In production this is real Atlassian Cloud API. In v1 demo, in-memory.

Endpoints:

    POST /jira/issues          · create a new JIRA issue
    GET  /jira/issues/{key}    · read an issue
    GET  /jira/issues          · list (optional filter by project / type / status)
    POST /jira/issues/{key}/transition · move through workflow
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
logging.basicConfig(level="INFO")

app = FastAPI(title="mcp-jira", version="0.1.0")


# ─── in-memory state ─────────────────────────────────────────────────────

_issues: dict[str, dict[str, Any]] = {}
_next = {"TECH": 100, "FEAT": 100, "BUG": 100}


# ─── request shapes ──────────────────────────────────────────────────────

class CreateIssue(BaseModel):
    project: str = "TECH"        # TECH | FEAT | BUG
    type: str = "Task"            # Task | Story | Bug | Epic
    summary: str
    description: str = ""
    priority: str = "P2"          # P0 | P1 | P2 | P3
    labels: list[str] = []
    reporter: str = ""            # which agent filed this
    linked_to: list[str] = []     # other issue keys


class Transition(BaseModel):
    new_status: str               # Open | In Progress | Done | Won't Do


@app.get("/health")
def health():
    return {"status": "ok", "issue_count": len(_issues)}


@app.post("/jira/issues")
def create_issue(req: CreateIssue) -> dict:
    project = req.project.upper()
    if project not in _next:
        _next[project] = 100
    key = f"{project}-{_next[project]}"
    _next[project] += 1

    issue = {
        "key": key,
        "project": project,
        "type": req.type,
        "summary": req.summary,
        "description": req.description,
        "priority": req.priority,
        "labels": req.labels,
        "reporter": req.reporter,
        "linked_to": req.linked_to,
        "status": "Open",
        "created_at": time.time(),
    }
    _issues[key] = issue
    log.info("jira.created key=%s reporter=%s summary=%s",
             key, req.reporter, req.summary[:60])
    return issue


@app.get("/jira/issues/{key}")
def read_issue(key: str) -> dict:
    issue = _issues.get(key)
    if not issue:
        raise HTTPException(404, "issue not found")
    return issue


@app.get("/jira/issues")
def list_issues(
    project: str | None = None,
    type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    items = list(_issues.values())
    if project:
        items = [i for i in items if i["project"] == project.upper()]
    if type:
        items = [i for i in items if i["type"] == type]
    if status:
        items = [i for i in items if i["status"] == status]
    return items


@app.post("/jira/issues/{key}/transition")
def transition(key: str, req: Transition) -> dict:
    issue = _issues.get(key)
    if not issue:
        raise HTTPException(404, "issue not found")
    issue["status"] = req.new_status
    issue["transitioned_at"] = time.time()
    return issue
