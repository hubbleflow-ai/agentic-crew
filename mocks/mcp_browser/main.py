"""mcp-browser · REAL web research surface using Playwright (Option B).

PM uses this for evidence-trail research. Real Chromium navigates real
URLs, captures real screenshots saved to the shared workspace volume.
UI renders those screenshots from the volume.

Architecture:

    pm.navigate(url="https://owasp.org/...")
        ↓
    mcp-browser BrowserManager.navigate(agent_id, url)
        ↓
    Real Chromium (headless) goes to URL, returns body text + title
        ↓
    pm.screenshot_and_annotate(url, looking_for, finding)
        ↓
    Real screenshot saved to /workspace/evidence/{agent_id}/evidence-XYZ.png
        ↓
    Evidence card appears in PM's view in the UI

Reliability tradeoff: real Chromium can fail (network, page errors).
For URLs in the demo scenario we've vetted, this is fine. For arbitrary
URLs (production use), agents need to handle nav failures gracefully.

Endpoints:

    POST /browser/navigate            · go to URL, return content
    POST /browser/screenshot_and_annotate · screenshot + add annotation
    GET  /browser/evidence/{agent_id} · list captured evidence
    DELETE /browser/{agent_id}        · close that agent's browser context
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from pydantic import BaseModel

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


# ─── browser manager · singleton Playwright per process ──────────────────

class BrowserManager:
    """One headless Chromium per process, isolated context per agent.

    Why per-agent context vs per-agent browser: contexts are cheap, browsers
    are not. A context gives agents independent cookies, localStorage,
    permissions · enough isolation for our use case.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._evidence: dict[str, list[dict[str, Any]]] = {}

    async def startup(self) -> None:
        log.info("browser.starting playwright")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",  # critical inside Docker
                "--no-sandbox",             # required when running as root
            ],
        )
        log.info("browser.ready chromium=%s", self._browser.version)

    async def shutdown(self) -> None:
        for ctx in self._contexts.values():
            try:
                await ctx.close()
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        log.info("browser.shutdown")

    async def _get_context(self, agent_id: str) -> BrowserContext:
        if agent_id not in self._contexts:
            self._contexts[agent_id] = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="HubbleflowCrew/0.1 (Chromium)",
            )
        return self._contexts[agent_id]

    async def navigate(self, agent_id: str, url: str) -> dict:
        ctx = await self._get_context(agent_id)
        page = await ctx.new_page()
        try:
            log.info("browser.navigate agent=%s url=%s", agent_id, url)
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            title = await page.title()
            # Get the visible body text · capped to 5k chars to keep agent prompts small
            text = await page.inner_text("body")
            content = text[:5000]
            return {
                "url": url,
                "title": title,
                "content": content,
                "truncated": len(text) > 5000,
            }
        except Exception as e:
            log.warning("browser.navigate_failed url=%s err=%s", url, e)
            return {"url": url, "title": "[error]", "content": "", "error": str(e)}
        finally:
            await page.close()

    async def screenshot_and_annotate(
        self,
        agent_id: str,
        url: str,
        looking_for: str,
        finding: str,
        cite_as: str = "",
    ) -> dict:
        ctx = await self._get_context(agent_id)
        page = await ctx.new_page()
        screenshot_id = f"evidence-{secrets.token_hex(3)}"
        # Save under /workspace so the UI can render via the same shared volume
        rel_path = f"evidence/{agent_id}/{screenshot_id}.png"
        abs_path = f"/workspace/{rel_path}"
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        try:
            log.info("browser.screenshot agent=%s id=%s url=%s",
                     agent_id, screenshot_id, url)
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            await page.screenshot(path=abs_path, full_page=True)
            title = await page.title()
        except Exception as e:
            log.warning("browser.screenshot_failed url=%s err=%s", url, e)
            await page.close()
            raise HTTPException(500, f"screenshot failed: {e}")
        finally:
            await page.close()

        entry = {
            "screenshot_id": screenshot_id,
            "url": url,
            "page_title": title,
            "captured_at": time.time(),
            "looking_for": looking_for,
            "finding": finding,
            "cite_as": cite_as or screenshot_id,
            "screenshot_path": rel_path,  # relative to /workspace · UI builds URL from this
        }
        self._evidence.setdefault(agent_id, []).append(entry)
        return entry

    def list_evidence(self, agent_id: str) -> list[dict]:
        return self._evidence.get(agent_id, [])

    async def close_agent(self, agent_id: str) -> None:
        ctx = self._contexts.pop(agent_id, None)
        if ctx:
            await ctx.close()


_manager = BrowserManager()


# ─── lifespan ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _manager.startup()
    yield
    await _manager.shutdown()


app = FastAPI(title="mcp-browser", version="0.1.0", lifespan=lifespan)


# ─── request shapes ──────────────────────────────────────────────────────

class Navigate(BaseModel):
    agent_id: str
    url: str


class ScreenshotAndAnnotate(BaseModel):
    agent_id: str
    url: str
    looking_for: str
    finding: str
    cite_as: str = ""


# ─── routes ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "active_contexts": len(_manager._contexts),
        "evidence_total": sum(len(v) for v in _manager._evidence.values()),
        "engine": "chromium" if _manager._browser else "not-ready",
    }


@app.post("/browser/navigate")
async def navigate(req: Navigate) -> dict:
    return await _manager.navigate(req.agent_id, req.url)


@app.post("/browser/screenshot_and_annotate")
async def screenshot_and_annotate(req: ScreenshotAndAnnotate) -> dict:
    return await _manager.screenshot_and_annotate(
        req.agent_id, req.url, req.looking_for, req.finding, req.cite_as,
    )


@app.get("/browser/evidence/{agent_id}")
async def list_evidence(agent_id: str) -> dict:
    return {
        "agent_id": agent_id,
        "evidence": _manager.list_evidence(agent_id),
    }


@app.delete("/browser/{agent_id}")
async def close_agent(agent_id: str) -> dict:
    await _manager.close_agent(agent_id)
    return {"agent_id": agent_id, "closed": True}
