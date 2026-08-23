"""mcp-github · code repo + PR surface for the Crew.

In production this is real GitHub API with OAuth. In v1 demo, we return
pre-scripted PR data for the CSV export scenario.

Endpoints:

    POST /github/read_file       · read a file's contents
    POST /github/write_file      · write to a branch
    POST /github/commit          · commit staged changes
    POST /github/open_pr         · open a pull request
    POST /github/post_comment    · post a review comment on a PR
    POST /github/approve         · approve a PR
    POST /github/request_changes · request changes on a PR
    GET  /github/pr/{pr_number}  · read a PR (diff, comments, status)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
logging.basicConfig(level="INFO")

app = FastAPI(title="mcp-github", version="0.1.0")


# ─── pre-scripted demo data ──────────────────────────────────────────────

_DEMO_PR_DIFF = """diff --git a/api/exports.py b/api/exports.py
new file mode 100644
index 0000000..e8a1c12
--- /dev/null
+++ b/api/exports.py
@@ -0,0 +1,87 @@
+from fastapi import APIRouter, Depends, BackgroundTasks
+from sqlalchemy.orm import Session
+from uuid import uuid4
+
+from app.auth import get_current_user, is_admin
+from app.tasks import queue_export
+from app.rate_limit import token_bucket  # switched per Reviewer
+
+router = APIRouter(prefix="/exports")
+
+
+@router.post("/csv")
+async def request_csv_export(
+    background_tasks: BackgroundTasks,
+    user = Depends(get_current_user),
+):
+    if not token_bucket.consume(
+        key=f"export:csv:{user.id}",
+        rate_per_hour=10,
+        burst=3,
+    ):
+        raise HTTPException(429, "Rate limit · 10 exports per hour")
+
+    export_id = str(uuid4())
+    mask_emails = not is_admin(user)
+    background_tasks.add_task(queue_export, user.id, export_id, mask_emails)
+
+    return {"export_id": export_id, "status": "queued"}
"""


# ─── in-memory state ─────────────────────────────────────────────────────

_files: dict[str, str] = {}      # path → content
_prs: dict[int, dict[str, Any]] = {}
_next_pr = 4821


# ─── request shapes ──────────────────────────────────────────────────────

class ReadFile(BaseModel):
    repo: str
    path: str
    ref: str = "main"


class WriteFile(BaseModel):
    agent_id: str
    branch: str
    path: str
    content: str


class OpenPR(BaseModel):
    branch: str
    title: str
    body: str = ""


class PostComment(BaseModel):
    pr_number: int
    author: str
    line: int = 0
    path: str = ""
    body: str


class ApproveRequest(BaseModel):
    pr_number: int
    reviewer: str


class RequestChanges(BaseModel):
    pr_number: int
    reviewer: str
    summary: str = ""


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "files": len(_files), "prs": len(_prs)}


@app.post("/github/read_file")
def read_file(req: ReadFile) -> dict:
    content = _files.get(req.path, "")
    return {"path": req.path, "content": content, "exists": bool(content)}


@app.post("/github/write_file")
def write_file(req: WriteFile) -> dict:
    _files[req.path] = req.content
    log.info("github.write path=%s bytes=%d", req.path, len(req.content))
    return {"path": req.path, "bytes": len(req.content)}


@app.post("/github/open_pr")
def open_pr(req: OpenPR) -> dict:
    global _next_pr
    pr_number = _next_pr
    _next_pr += 1
    pr = {
        "pr_number": pr_number,
        "branch": req.branch,
        "title": req.title,
        "body": req.body,
        "status": "open",
        "review_status": "pending",
        "diff": _DEMO_PR_DIFF,
        "comments": [],
        "created_at": time.time(),
    }
    _prs[pr_number] = pr
    log.info("github.pr_opened pr=%d branch=%s", pr_number, req.branch)
    return pr


@app.post("/github/post_comment")
def post_comment(req: PostComment) -> dict:
    pr = _prs.get(req.pr_number)
    if not pr:
        raise HTTPException(404, "pr not found")
    entry = {
        "author": req.author,
        "line": req.line,
        "path": req.path,
        "body": req.body,
        "at": time.time(),
    }
    pr["comments"].append(entry)
    return entry


@app.post("/github/approve")
def approve(req: ApproveRequest) -> dict:
    pr = _prs.get(req.pr_number)
    if not pr:
        raise HTTPException(404, "pr not found")
    pr["review_status"] = "approved"
    pr["approved_by"] = req.reviewer
    return pr


@app.post("/github/request_changes")
def request_changes(req: RequestChanges) -> dict:
    pr = _prs.get(req.pr_number)
    if not pr:
        raise HTTPException(404, "pr not found")
    pr["review_status"] = "changes_requested"
    pr["changes_requested_by"] = req.reviewer
    pr["change_summary"] = req.summary
    return pr


@app.get("/github/pr/{pr_number}")
def read_pr(pr_number: int) -> dict:
    pr = _prs.get(pr_number)
    if not pr:
        raise HTTPException(404, "pr not found")
    return pr


if __name__ == "__main__":
    # Run this service on its own, under a debugger, without the cluster.
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9002)
