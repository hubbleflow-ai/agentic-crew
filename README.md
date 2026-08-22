# Hubbleflow Crew · Agentic AI Engineering Team

An autonomous engineering team you compose container by container. Spawn
specialists. Watch them collaborate. Each in its own Docker sandbox with
its own memory. Built on DeepAgents (LangChain) + Gemini + Sandboxes + Browser Use.

Target audience: software engineers, technical founders, CTOs.

## What's in this folder

| File | What it is |
|---|---|
| `PRODUCT_SPEC.md` | Full product specification · the design doc. Read this first. |
| `PRODUCT_SPEC.html` | Same spec, styled HTML with TOC, tables, callouts. Open in browser. |
| `ui-mockup.html` | Live visual prototype of the operator console. Open in browser. |

## Project status

**Build phase · in progress.** Spec + UI mockup + initial scaffolding
done. Hybrid integration (Option B) locked: `mcp-sandbox` and
`mcp-browser` use real Docker exec + real Playwright Chromium.
`mcp-tickets`, `mcp-github`, `mcp-jira` stay as in-memory mocks for
demo reliability (no OAuth flakiness).

**Backend now runs end-to-end (headless).** Wired up:
- Full roster of 6 agents · EM, PM, Backend, **Frontend**, **QA**, Reviewer.
- Control plane auto-spawns the EM on `POST /tasks` and delivers the
  Founder's request via a race-free bootstrap env (not lossy pub/sub).
- The EM has real `spawn_agent` + `escalate_to_founder` tools, so it
  actually assembles the team and raises HITL gates (`POST /escalations`).
- EM→worker assignments ride the same race-free bootstrap mechanism.
- Role→module mapping fixed (canonical role names → agent dirs).

**Operator console is built.** `apps/web/` is now a working Next.js app
that faithfully ports `ui-mockup.html` and drives it with live data:
- Submit a task from the chat → control plane spawns the crew.
- Live agent tabs (one per container) with real status dots; click any
  to watch its workspace render code edits, terminal runs, browser
  research, tickets, and reasoning from the WebSocket event stream.
- Real KPIs (pods, elapsed, tokens, cost) — backed by `usage` events.
- Footer **+ Spawn agent** really spawns a container (cap-enforced);
  chat input sends founder messages / answers escalations to the crew.

Run the UI with `docker compose --profile ui up --build` (it's behind
the `ui` profile so a plain `docker compose up` still skips it).

Updated build budget: ~22 focused hours total.

## To run (when ready)

```bash
# 1. Set up env
cp .env.example .env
# Edit .env · set GOOGLE_API_KEY (Gemini, Google AI Studio)

# 2. Build both images (agent + sandbox)
./scripts/build-images.sh

# 3. Boot the stack
docker compose up

# 4. Open the UI
open http://localhost:3000
```

## Quick context for newcomers

For Session 7 of the Agentic AI Mastery cohort, we wanted to showcase
truly autonomous agents (Manus / Cognition class) using Claude SDK +
Sandboxes + Browser Use. After extensive exploration of variants
(incident response, observability detection, investment research,
autonomous SRE), we landed on the cleanest, most demoable form:

**Crew · a platform where the Founder/CTO spawns AI engineering
specialists on demand to build features end-to-end.**

Each agent is a real Docker container with its own:
- DeepAgents (LangChain) loop on Gemini
- System prompt for its role
- Tool catalogue (MCP servers)
- Sandbox for code execution
- Browser (Chromium) where relevant
- Memory volume

They collaborate via Redis pub/sub + a shared workspace volume. The
human (Founder) is the supervisor · approves the plan, policy decisions,
final PRs, deploys.

**Primary demo:** Feature Build · the Founder types "Build me a CSV
export with email delivery for the user dashboard." Crew spawns 5
agents (EM, Backend, Frontend, QA, Reviewer), they collaborate, ship a
real PR in ~12 minutes.

See `PRODUCT_SPEC.md` for the full architecture, agent roster, demo
flow, and phased build plan. Open `ui-mockup.html` in any browser for
the visual language.
