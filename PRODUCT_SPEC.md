# Hubbleflow Crew · Agentic AI Engineering Team

**Product specification · v0.1 · design phase**

---

## 0 · TL;DR

Crew is a platform where the Founder / CTO spawns an autonomous AI
engineering team on demand. Each specialist agent runs in its own real
Docker container with its own Claude Agent SDK loop, system prompt,
tools, sandbox, and memory.

A canonical run:

1. Founder types a feature request in natural language
2. Engineering Manager (EM) spawns automatically, asks clarifying
   questions, proposes a plan
3. Founder approves the plan (HITL gate #1)
4. EM spawns the team (Backend, Frontend, QA, Reviewer) · real Docker
   containers materialize
5. Agents write real code in real sandboxes (CodeAct), collaborate via
   a shared workspace, peer-review each other's work
6. Reviewer flags issues, Backend revises (peer-review loop)
7. EM escalates policy decisions to Founder (HITL gate #2)
8. Final PR presented to Founder (HITL gate #3)
9. Founder approves merge + deploy (HITL gate #4)
10. Crew winds down · containers killed except EM (kept warm for next request)

The primary demo: 12 minutes, 5 containers, real Python written,
real React written, real peer-review, four HITL gates, a real PR merged.

---

## 1 · Locked design decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Each agent = real Docker container | Spawnable, isolated, visible (`docker ps`). Real engineering, not threads. |
| 2 | Claude Agent SDK as the loop | Battle-tested, supports tool use, structured outputs |
| 3 | Real role names · EM, Backend, Frontend, QA, Reviewer, etc. | No clever names. Engineers recognize their own org chart. |
| 4 | Founder is the supervisor (HITL) at 4 gates | Plan, Policy, Pre-merge, Deploy. Bidirectional · agents can also ask Founder. |
| 5 | Code Reviewer is its own agent (peer review built-in) | Adversarial validation. Real Critic-Actor pattern. |
| 6 | Inter-agent communication via Redis pub/sub | Real concurrency, real message bus, visible flow |
| 7 | Shared workspace = mounted volume (postmortem.md, workspace.md) | Sectioned ownership to avoid conflicts. CRDT later if needed. |
| 8 | Voice mode + Founder chat as primary UX | Reuses Trip Planner voice stack (Cloud TTS + Web Speech) |
| 9 | Primary demo: Feature Build (CSV export with email delivery) | Deterministic path, CodeAct heavy, four HITL moments naturally |
| 10 | Sentinel mode (autonomous observability) deferred to v2 | Live demo risk from LLM non-determinism. Crew's path is deterministic. |

---

## 2 · Why this build exists

Software engineering teams are bottlenecked on coordination. A feature
typically requires:

- A PM to scope it
- A backend engineer to write the API
- A frontend engineer to write the UI
- A QA engineer to write tests
- A reviewer (senior eng) to sign off
- A deploy approval from leadership

That's 5-7 humans + days of calendar coordination for what could be
~30 minutes of focused work end-to-end.

Crew compresses this. Each specialist becomes a Docker container. They
work in parallel. The human (Founder) stays in the loop only at
meaningful gates · not for every keystroke.

**For the cohort, this build demonstrates:**

- CodeAct in production (every agent's primary action is writing Python / TypeScript)
- Multi-agent collaboration (real concurrency, shared state)
- Peer review as an adversarial pattern
- HITL as a first-class architectural concern
- Real Docker per agent (visible isolation, real resource tracking)

This is a stronger CodeAct demo than incident response because every
agent here writes substantive code. And it's safer than autonomous
observability because the path is deterministic.

---

## 3 · Functional requirements

### Primary use cases

1. **Feature build from request** · Founder describes, Crew builds, ships PR
2. **Code review on existing PR** · Crew reviews a PR, posts comments,
   suggests changes
3. **Refactor request** · "Modernize this Express app to Fastify" ·
   Crew migrates file by file
4. **API integration** · "Add Stripe to my checkout" · Crew researches
   docs, implements, tests
5. **Hot fix during incident** · operator describes the bug, Crew
   reproduces + fixes + ships (this is the Sentinel-light path)

### Performance targets

| Metric | Target |
|---|---|
| Time from request → plan proposal | <60s |
| Time from plan approval → first agent spawned | <10s |
| Container boot time (per agent) | <8s |
| Time from spawn → first useful work | <30s |
| Total time for small feature build (1-2 files) | <8 min |
| Total time for medium feature build (5-10 files) | <20 min |
| HITL gate response time | <1s |
| Cost per medium feature build | <$5 (Claude tokens + sandbox compute) |

---

## 4 · The agent roster

**10 agent roles** ship in v1. Each is a Docker image with a tuned
system prompt, scoped tool catalogue, and resource profile.

| Role | System prompt focus | Tools (MCPs) | Sandbox? | Browser? |
|---|---|---|---|---|
| **Engineering Manager** | Decomposes requests, plans, orchestrates, escalates to Founder | mcp-jira, mcp-github (read), mcp-slack | No | Yes |
| **Backend Engineer** | Writes server-side code, tests, debugs | mcp-sandbox, mcp-github (write), mcp-docker | Yes | No |
| **Frontend Engineer** | Writes UI, tests visually | mcp-sandbox, mcp-github (write), mcp-chromium | Yes | Yes |
| **QA Engineer** | Writes tests, finds edge cases | mcp-sandbox, mcp-chromium, mcp-github | Yes | Yes |
| **Code Reviewer** | Reviews diffs, runs linters, posts comments | mcp-github, mcp-sandbox (linters only), mcp-codebase-rag | Yes (linters only) | No |
| **DevOps Engineer** | CI config, deploy scripts, infra concerns | mcp-github, mcp-docker, mcp-terraform | Yes | No |
| **Security Engineer** | Audits diffs for vulns, secrets, attack surface | mcp-sandbox (SAST), mcp-cve, mcp-github | Yes | No |
| **Product Manager** | Translates business intent, scope trade-offs | mcp-jira, mcp-github (read), mcp-slack (read) | No | Yes |
| **SRE** | Incident response (the Sentinel-light path) | mcp-sentry, mcp-datadog, mcp-github | No | Yes |
| **Tech Writer** | Documents APIs, READMEs, changelogs | mcp-github (write), mcp-chromium (read docs) | No | Yes |

### Universal properties of every agent

- Claude Agent SDK as the loop
- Registers itself with the control plane on boot
- Subscribes to `crew/incident/<id>/messages` Redis channel
- Mounts the shared workspace volume at `/workspace`
- Has a per-session token budget (default 100k tokens)
- Has a per-session sandbox cost cap (default $1)
- Emits OpenTelemetry traces to Phoenix
- Container image ~150-250 MB depending on role

### Spawning · what actually happens

```bash
# Triggered from the UI when user clicks "+ Spawn"
docker run -d \
  --name crew-backend-<8-char-hash> \
  --network crew-net \
  -v ./workspaces/<incident_id>:/workspace \
  -e CREW_ROLE=backend_engineer \
  -e CREW_INCIDENT_ID=<incident_id> \
  -e CREW_AGENT_ID=<8-char-hash> \
  -e ANTHROPIC_API_KEY=<from-secret> \
  -e CREW_TOKEN_BUDGET=100000 \
  -e CREW_REDIS_URL=redis://crew-redis:6379 \
  crew-agent-backend:v1
```

Boot to ready: ~8 seconds. Agent introduces itself in chat once ready.

---

## 5 · Communication architecture

### The shared state stack

Three layers · same pattern from the DarkStore + Trip Planner builds:

```
┌──────────────────────────────────────────────────────────┐
│  Layer 1 · Ephemeral                                      │
│  Lives in container memory. Dies with container.          │
│  LLM context, scratch pad, in-flight reasoning.           │
├──────────────────────────────────────────────────────────┤
│  Layer 2 · Session (per-task)                             │
│  Lives across all agents working on the same task.        │
│  • Redis pub/sub for messages                             │
│  • Shared volume for documents:                           │
│    - workspace.md (the running task brief)                │
│    - prd.md (if PM is on the crew)                        │
│    - review_comments.md (Reviewer outputs)                │
│  Sectioned ownership · each agent writes its own section. │
├──────────────────────────────────────────────────────────┤
│  Layer 3 · Long-term (per-team)                           │
│  Lives forever in Postgres + pgvector.                    │
│  Past tasks, learned conventions, calibration history.    │
│  Written by Librarian (separate service) at task close.   │
└──────────────────────────────────────────────────────────┘
```

### Inter-agent messages

Format on the Redis channel:

```json
{
  "from": "backend_engineer/8c2f",
  "to": "code_reviewer",
  "type": "request_review",
  "at": "2026-06-11T14:23:04Z",
  "payload": {
    "pr_branch": "feat/csv-export",
    "files_changed": ["api/exports.py", "tests/test_exports.py"],
    "summary": "First pass implementation. Tests pass. Ready for review."
  }
}
```

Types: `intro` · `claim` · `request_review` · `review_complete` ·
`question` · `answer` · `escalate_to_founder` · `task_complete` · `error`.

Every message is also persisted in Postgres for audit / replay.

---

## 6 · HITL gates · four moments where the Founder is in the loop

Bidirectional · Founder can also barge in any time via the chat.

| # | Gate | When it fires | What the Founder sees |
|---|---|---|---|
| 1 | **Plan approval** | EM presents the task breakdown + recommended team | Plan card with tasks, est. time, est. cost, team composition. Approve / Modify / Cancel. |
| 2 | **Policy escalation** | Agent hits a decision it can't make alone (PII handling, library choice, breaking API change, security trade-off) | Question card with context + options. Founder picks. |
| 3 | **Pre-merge approval** | All work complete, PR ready | Diff view + test results + reviewer signoff + screenshots. Approve / Request changes / Reject. |
| 4 | **Deploy approval** | PR merged, staging green | Deploy plan + rollback plan. Approve / Hold. |

### Always-on operator chat

The Founder can speak / type at any time. EM picks up the message,
routes it to the relevant agent. Example:

> Founder mid-build: "Actually use SendGrid not SES."

EM routes this to Backend. Backend acknowledges, switches in next
iteration. The Founder doesn't have to wait for a formal gate · the
chat is always live.

---

## 7 · Peer review architecture

Code Reviewer is a normal agent in the roster, just with a tuned system
prompt and tool catalogue. But it plays an adversarial role · its job
is to disagree with Backend's work.

### Review loop

```
Backend pushes commit to feature branch
        │
        ▼
Reviewer auto-spawns (or already in roster)
        │
        ▼
Reviewer reads diff via mcp-github
Reviewer runs linters in own sandbox (ruff, mypy, bandit, etc.)
Reviewer queries codebase-rag for similar past patterns
        │
        ▼
Reviewer posts inline comments via mcp-github
        │
        ├──→ "request_changes": Backend addresses
        │      → loops back to top
        │
        └──→ "approve": signals EM
              → EM escalates to Founder gate #3
```

### Multiple reviewers

For higher-stakes changes, multiple reviewers can be spawned in parallel:

- **Senior Engineer** · style + correctness
- **Security Engineer** · vulns + secrets + PII
- **Performance Engineer** · benchmark regressions

Each posts independently. Backend must satisfy all of them before EM
escalates.

This is the Critic-Actor pattern from Session 6's debate slide made
concrete.

---

## 8 · UX vision

Visual language extends Sessions 5 + 6 · cream background, peach
highlights, terracotta accent, minimalist geometric shapes.

**See `ui-mockup.html` in this folder for the visual prototype.**

### Layout (two panels)

1. **Header strip** · Hubbleflow Crew brand, project name, live indicator, "Hide Workspace" button
2. **Left panel** · KPI strip → Tabs (Chat / Roster / Activity) → Tab content → Voice bar pinned at bottom
3. **Right reveal panel (collapsible)** · multi-tab view of what's happening
   - Code · live editor showing current file being edited (which agent + which file)
   - Browser · live Chromium screenshot (where Frontend / QA is testing)
   - Containers · `docker ps`-style view with CPU, RAM, tokens used per agent
   - Diff · the current PR's diff (when one exists)
   - Plan · EM's task breakdown + progress
4. **Footer** · three pre-baked demo buttons (Build · Investigate · Refactor) + time controls

### Color palette (locked, matches cohort design system)

```
Background       #faf6f0   cream
Surface          #ffffff   panels
Edge             #ebe4dc   subtle borders
Ink              #1f1a16   text
Muted            #7a7068   secondary text
Accent           #d97757   terracotta · action color
Accent soft      #f2dcd0   highlights · paths · active state
Hot zone         #f5e6d8   warm tint
Ok               #2d7a2d   green for passing tests, approved
Danger           #c2410c   red for failures, escalations
Shelf / dark     #2a2a2a   obstacles, code background
```

### Type

- Headers: **Source Serif 4**
- Body / UI: **Inter**
- Code / numbers / IDs: **ui-monospace**

### Critical micro-interactions

| Trigger | Animation |
|---|---|
| Agent spawning | Container card slides in with "spawning..." spinner, transitions to "ready" |
| Agent thinking | Subtle pulse on the agent's avatar |
| Agent writing code | Live diff snippets stream into Code tab |
| Test passing | Green check icon, soft chime |
| Test failing | Red X icon, agent's reasoning surfaces in chat |
| Reviewer requesting changes | Yellow bar above the PR · "Changes requested" |
| HITL gate | Modal pops up, dimmed background, Approve / Modify / Cancel |
| Operator speaks | Live transcript types into chat as words are recognized |

---

## 9 · The primary demo · CSV export with email delivery

### Setup

Founder opens Crew. Empty roster. Home screen shows three buttons:

```
What should the team build today?

  🚀 Build      🔎 Investigate      🔧 Refactor
  "Build me     "Diagnose            "Modernize
   a feature"   an incident"         a codebase"
```

Founder clicks `Build` · or types directly in the chat:

> "Build me a CSV export feature for the user dashboard. Async with a
> queue, send via email when ready, rate-limited to 10 per user per hour."

### Timeline

| Time | Who | What |
|---|---|---|
| 0:00 | Founder | Issues the request |
| 0:10 | Engineering Manager spawns | Reads request, asks 1-2 clarifying questions ("Which auth system? UTF-8 or BOM-prefixed for Excel?") |
| 1:00 | Founder | Answers |
| 1:30 | EM | Proposes plan · 4 tasks, recommended team of Backend + Frontend + QA + Reviewer. Modal pops up. |
| 2:00 | Founder | Approves plan (HITL #1) |
| 2:10 | EM | Spawns 4 agent containers · visible in Roster |
| 2:30 | Backend Engineer | Starts writing Python · `api/exports.py` |
| 2:30 | Frontend Engineer | Starts writing React · `components/ExportButton.tsx` (parallel) |
| 4:00 | Backend | Runs `pytest` · 3 tests fail |
| 4:15 | Backend | Reads test output, fixes, re-runs · passes |
| 5:00 | QA Engineer | Writes edge-case tests, finds Backend missed pagination |
| 5:30 | Backend | Adds pagination, tests pass |
| 7:00 | Code Reviewer | Reads diff, runs linters, posts review |
| 7:30 | Reviewer | Flags: "Use token bucket for rate limit. PII risk · CSV includes email column." |
| 8:00 | Backend | Switches to token bucket, asks EM about PII |
| 8:30 | EM | Escalates PII question to Founder (HITL #2) |
| 8:45 | Founder | "Mask emails for non-admin exports" |
| 9:00 | Backend | Implements masking |
| 10:00 | Reviewer | Re-reviews, approves |
| 10:30 | Frontend | Opens Chromium, tests the new button end-to-end |
| 11:30 | EM | Presents final PR to Founder · diff, tests, screenshots, reviewer signoff |
| 12:00 | Founder | "Merge it" (HITL #3) |
| 12:15 | Crew | Merges PR, mocks deploy to staging |
| 12:30 | All agents | Wind down · containers killed except EM |

**Total: ~12 minutes. 4 active agents (5 with EM). Real code written. Real peer review with substantive feedback. Real HITL at 3 gates.**

---

## 10 · Architectural patterns demonstrated

Maps directly to demo moments students will see:

| Pattern | Where to see it |
|---|---|
| **Supervisor + delegation** | EM orchestrates Backend, Frontend, QA, Reviewer |
| **CodeAct** | Every agent's primary action is writing executable code |
| **Sandbox isolation** | Each agent has its own sandbox, code runs in isolation |
| **Browser use** | Frontend + QA + EM + PM use Chromium for visual tasks |
| **Peer review (Critic-Actor)** | Code Reviewer adversarially validates Backend's work |
| **HITL bidirectional** | Founder approves gates · Agents can ask Founder |
| **Real concurrent multi-agent** | Backend + Frontend write code in parallel sandboxes |
| **Spawn / kill lifecycle** | Real Docker containers materialize and dissolve |
| **Shared workspace blackboard** | All agents read/write the workspace doc with sectioned ownership |
| **Confidence-aware escalation** | Agents escalate when not confident, otherwise proceed |

---

## 11 · Tech stack

| Layer | Choice | Why |
|---|---|---|
| LLM | Claude (Sonnet primary, Opus for Reviewer, Haiku for EM coordination) | Best at agentic loops, tool use, code |
| Agent framework | Claude Agent SDK | Native loop, tool calling, structured outputs |
| Per-agent runtime | Docker containers (one per agent) | Real isolation, visible spawning |
| Container orchestration | Docker Compose for demo, K8s for prod | Cohort-grade simplicity |
| Inter-agent comms | Redis Pub/Sub | Lightweight, real concurrency |
| Shared workspace | Mounted volume (Docker bind mount or named volume) | Simple, supports CRDT later |
| Long-term memory | Postgres + pgvector | Standard, queryable |
| Control plane | FastAPI service · spawn / kill / route messages | Single source of truth |
| Frontend | Next.js 14 | Consistency with Sessions 5/6 |
| Real-time push | Server-Sent Events | Same as Trip Planner |
| Voice (TTS) | Google Cloud TTS Streaming (Chirp3-HD-Charon) | Reused from Trip Planner |
| Voice (STT) | Browser Web Speech API | Reused from Trip Planner |
| Observability | OpenTelemetry → Phoenix | Cross-agent trace tree |
| Deployment | Docker Compose (single-host) | Lab-grade |
| Sandboxes | Daytona / E2B / local Docker sandbox | Real Python/Node execution per agent |
| Browser | Playwright + headless Chromium · one container per agent that needs it | Real DOM interaction |

### Service count

| Type | Count |
|---|---|
| Agent containers (spawned on demand) | 0-10 |
| Control plane | 1 |
| Redis | 1 |
| Postgres | 1 |
| Phoenix | 1 |
| Frontend (Next.js) | 1 |
| MCP servers (mock for demo, real for prod) | ~6 |

Typical session: control plane + 5 agents + infra = ~10 containers running.

---

## 12 · Build plan · the honest 14-hour budget

### Hour 1-2 · Repo skeleton + Docker setup
- Folder structure: `crew/` with `apps/control-plane`, `apps/web`, `agents/`, `infra/`
- `docker-compose.yml` with: control-plane, web, redis, postgres, two agent images (EM, Backend) as baseline
- Agent base Dockerfile · Python + Claude SDK + claude-agent-sdk + requirements
- One reusable image, role specified via env var

### Hour 3-5 · Three agent loops · EM, Backend, Reviewer
- Each agent: Claude SDK loop with system prompt + mock MCPs returning canned data for v1
- Subscribe to Redis channel `crew/task/<id>/messages`
- Publish messages to the channel as they reason
- Mock `mcp-sandbox`, `mcp-github`, `mcp-jira` return realistic pre-scripted data for demo reliability

### Hour 6-7 · Control plane API (FastAPI)
- `POST /agents/spawn` · takes role, runs `docker run` via docker.sock, returns container_id
- `GET /agents` · lists active containers + their stats
- `DELETE /agents/{id}` · kills container
- `WS /stream` · relays Redis pub/sub to the browser
- Uses `docker` Python SDK · ~50 lines for this whole layer

### Hour 8-10 · Next.js UI
- Bootstrap from the mockup we already wrote (CSS + structure exists, just React-ify)
- Roster panel · live from `GET /agents` polling every 1s
- Spawn modal · calls `POST /agents/spawn`
- Chat view · WS stream from control plane
- Right panel with 3 tabs (Code · Diff · Containers)
- HITL approval modal (Plan approval gate)

### Hour 11-12 · Wire up the demo scenario
- "Build CSV export" button on the UI
- Pre-recorded scenario data: realistic agent personas + mock canned responses for high-reliability live demo
- Agents reason over it with real Claude calls
- Output: real PR markdown rendered in the UI, real workspace.md saved to volume

### Hour 13-14 · Polish + dry run
- One full end-to-end demo · time it, fix rough edges
- Add the spawn-during-demo flow (spawn QA mid-build)
- Test HITL gate UX

### Hour 15 buffer
- LLM gotchas, Docker networking weirdness, UI polish

**Total honest estimate: 14-15 focused hours.**

---

## 13 · What's in v1 vs deferred

### In v1 (the day 1 demo) · HYBRID integration (Option B)
- 3 agent roles (EM, Backend, Reviewer) · enough for the CSV export demo
- 4 with PM (added per governance constraints, all four ship v1)
- Control plane spawning containers via docker.sock
- Redis pub/sub for messages
- Shared volume for workspace.md
- Next.js UI with Chat / Roster / Code / Diff / Containers
- One demo scenario (Feature Build · CSV export)
- 4 HITL gates wired but simplified (mostly Approve buttons)

### MCP servers · what's REAL vs MOCKED in v1

**Real integrations (Option B · genuine CodeAct):**
- `mcp-sandbox` · REAL Docker exec · per-task sandbox container ·
  Backend's code actually executes, pytest actually runs, linters
  actually check
- `mcp-browser` · REAL Playwright + headless Chromium · PM actually
  navigates real URLs, takes real screenshots

**Mocked for demo reliability (no OAuth flakiness in cohort demo):**
- `mcp-tickets` · in-memory dict · production swaps to JIRA/Linear API
- `mcp-github` · scripted PR data · production swaps to GitHub OAuth
- `mcp-jira` · in-memory dict · production swaps to Atlassian Cloud API

The architecture is the same · mocked MCPs and real MCPs both
implement the same HTTP API. Swapping to real OAuth in v1.1 is a
config change, not a redesign.

### Deferred to v2
- Frontend + QA + DevOps + Security + PM + SRE + TechWriter agents (7 more roles)
- Real GitHub integration (OAuth, real PRs)
- Real JIRA integration
- Real Slack integration
- Browser tab in the reveal (Chromium screenshots)
- Long-term memory layer (vector DB + Librarian service)
- Checkpoint + replay
- Sentinel mode (autonomous observability)
- Cost dashboards
- Multi-tenant

The v1 → v2 jump is mostly adding agents and integrations, not
re-architecting. The platform shape is the same.

---

## 14 · Future scope · what Crew could become

If Crew works in the cohort demo, here's the natural product evolution:

| Phase | What gets added | When |
|---|---|---|
| **v1** | 3 roles, Feature Build demo, mock MCPs | Session 7 (week 1) |
| **v1.1** | 7 more roles, real GitHub/JIRA OAuth | Session 7 follow-up |
| **v1.2** | Sentinel mode (autonomous observability) | Possible Module 8 |
| **v2** | Checkpoint + replay, long-term memory | Capstone material |
| **v2.1** | Custom agent creation (define role + tools via UI) | Platform play |
| **v3** | Multi-tenant SaaS · pricing, billing, OAuth | If actually shipping |

---

## 15 · Open questions resolved

| Question | Decision |
|---|---|
| Real Docker vs Python subprocesses? | Real Docker · visibility + isolation worth the complexity |
| Real LLM calls or pre-recorded? | Real LLM calls for the most reliable parts, scripted fallbacks for the few critical reasoning steps that need determinism |
| How many agent roles in v1? | 3 (EM, Backend, Reviewer) · enough for demo, expandable |
| Mock MCPs or real? | Mock in v1 for reliability, real in v1.1 with OAuth flows |
| Single demo or carousel? | Single demo (Feature Build · CSV export) for v1. Investigate + Refactor are stubs that show "this would work but click here to see the canonical demo" |
| Voice mode in v1? | Yes · reuse Trip Planner stack, trivial integration |
| Phoenix observability? | Yes · cross-agent trace tree is a key cohort moment |

---

## 16 · Next steps

1. **Visual approval** · review `ui-mockup.html` and confirm the aesthetic
2. **Lock the demo scenario script** · the exact agent messages, mock data, HITL flow
3. **Set up the repo skeleton** · Docker Compose, shared modules, voice stack
4. **Start Phase 1 build** (the 14-hour day 1)
5. **Dry run with cohort-style audience** before the actual session

---

*Spec authored at design phase · subject to refinement as build progresses.*
*Hubbleflow Crew · v0.1 · 2026-06-11*
