"""mcp-tickets · source-of-truth ticket store for the Crew.

In production this is backed by JIRA / Linear. In v1 demo, it's an
in-memory dict that survives the run. The EM authors tickets, all
other agents read them.

Endpoints:

    POST /tickets                · create or update a ticket
    GET  /tickets/{ticket_id}    · read a ticket
    GET  /tickets                · list (optionally filter by project_id)
    POST /tickets/{ticket_id}/comments · append a comment
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
logging.basicConfig(level="INFO")

app = FastAPI(title="mcp-tickets", version="0.1.0")

_tickets: dict[str, dict[str, Any]] = {}


class TicketBody(BaseModel):
    project_id: str
    title: str
    api_contract: str = ""
    acceptance_criteria: list[str] = []
    assignments: dict[str, str] = {}  # role → description
    evidence_cited: list[str] = []    # screenshot ids
    status: str = "draft"             # draft | spec_complete | in_progress | done


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ticket_count": len(_tickets)}


@app.post("/tickets")
def write_ticket(body: TicketBody) -> dict:
    ticket_id = f"TICKET-{body.project_id.split('-')[-1].upper()}"
    _tickets[ticket_id] = {
        "ticket_id": ticket_id,
        "project_id": body.project_id,
        "title": body.title,
        "api_contract": body.api_contract,
        "acceptance_criteria": body.acceptance_criteria,
        "assignments": body.assignments,
        "evidence_cited": body.evidence_cited,
        "status": body.status,
        "comments": _tickets.get(ticket_id, {}).get("comments", []),
        "updated_at": time.time(),
    }
    log.info("ticket.written ticket_id=%s status=%s", ticket_id, body.status)
    return _tickets[ticket_id]


@app.get("/tickets/{ticket_id}")
def read_ticket(ticket_id: str) -> dict:
    ticket = _tickets.get(ticket_id)
    if not ticket:
        raise HTTPException(404, "ticket not found")
    return ticket


@app.get("/tickets")
def list_tickets(project_id: str | None = None) -> list[dict]:
    items = list(_tickets.values())
    if project_id:
        items = [t for t in items if t["project_id"] == project_id]
    return items


class CommentBody(BaseModel):
    author: str
    body: str


@app.post("/tickets/{ticket_id}/comments")
def add_comment(ticket_id: str, comment: CommentBody) -> dict:
    if ticket_id not in _tickets:
        raise HTTPException(404, "ticket not found")
    entry = {
        "author": comment.author,
        "body": comment.body,
        "at": time.time(),
    }
    _tickets[ticket_id].setdefault("comments", []).append(entry)
    return entry


if __name__ == "__main__":
    # Run this service on its own, under a debugger, without the cluster.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9001)
