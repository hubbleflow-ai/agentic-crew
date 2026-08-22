# Hubbleflow Crew · Overhaul Plan

Working document. Written 2026-08-22. Survives context loss — read this first.

**Goal:** rebuild this repo so it properly uses the DeepAgents harness, runs on
Kubernetes, and is clean enough to be the teaching artifact for a cohort session
on *what an agent harness is*.

---

## 1 · Decisions already taken

| Decision | Choice | Why |
|---|---|---|
| Founder's generic questions (#3) | **(a)** EM is sole front door, forwards to PM | keeps one orchestrator / one chokepoint |
| Prompt caching | **dropped for now** | see §6 — prompts are far below the cache floor |
| E2B sandboxing | **deferred** — Docker sandbox stays | but drop the `docker.sock` mount regardless |
| Old control-plane endpoints | **removed**, no back-compat | `/tasks` becomes `/projects/{id}/tasks` |
| Notebook 02 (current) | will break and be replaced | superseded by the new ladder |

### Still open

- **Kubernetes: hybrid or full?**
  - *hybrid* — agent pods in cluster, infra in Compose (what mind-palace does)
  - *full* — everything in the cluster
  - **Recommendation: full.** Requirements 4, 6 and 10 are things k8s does
    natively (ResourceQuota, label-selector counts, securityContext) that we
    would otherwise hand-roll in memory.
  - Cost of full: cohort uses `kubectl get pods`, not `docker ps`; images must
    be `minikube image load`ed.

---

## 2 · The ten requirements

| # | Requirement | Status today |
|---|---|---|
| 1 | Each agent in its own container; 3 backend engineers = 3 containers | ✅ already works |
| 2 | Progressive disclosure of skills | ❌ full prompt every call |
| 3 | EM/PM decide who answers a generic question | ⚠️ EM triages alone; PM never sees founder |
| 4 | Max caps per agent type | ⚠️ per-task only, in-memory |
| 5 | Chat named "New Project" until scope is concrete, then renamed | ❌ no project concept |
| 6 | Multiple concurrent projects | ⚠️ possible; state in-memory, caps not global |
| 7 | Extremely clean code, proper patterns | ❌ 544-line `main.py` mixing concerns; 34 ruff errors |
| 8 | Proper README | ❌ current one stale in 3 places |
| 9 | Proper harness use | ❌ 4 of 18 `create_deep_agent` params used |
| 10 | Code execution in sandbox containers (E2B if possible) | ⚠️ Docker + `docker.sock` mounted |

**Known conflicts, already flagged:**

- **#4 × #6** — caps are per-task, so 3 projects = 3× the containers. Needs
  both a per-project cap *and* a global cap, counted from live pods by label,
  not an in-memory dict.
- **#2 × caching** — progressive disclosure makes prompts *smaller*, moving
  further from the 4096-token cache floor. Cannot have both. Caching dropped.
- **#10 × #1** — E2B sandboxes are remote microVMs and never appear in
  `docker ps`/`kubectl`, which costs the "cohort can see it" property.

---

## 3 · Phases

### Phase 0 · Foundations — **DONE**
- `git init`, baseline commit `8da3732`
- Fixed `.gitignore`: pattern was `workspace/tasks/` but real dirs are
  `workspace/task-<id>/` and `workspace/evidence/` — 44 files of agent output
  (incl. screenshots) were being staged. Now `workspace/*` + `.gitkeep`.
- `pyproject.toml` with ruff / mypy / pytest. Baseline: **34 lint errors**.
- `agents/shared/logging_setup.py` — structured JSON logging, stdout + Loki,
  `bind_context()` for project/task/agent/role. Clean under ruff and mypy.

### Phase 1 · Layering and middleware
Split `apps/control_plane/main.py` (544 lines, mixes HTTP + orchestration +
Docker) into:

```
domain/     Project, Task, Agent, Ticket — pure, no I/O
ports/      protocols: AgentRuntime, SandboxBackend, EventBus
adapters/   docker_runtime, k8s_runtime, docker_sandbox, e2b_sandbox, redis_bus
api/        FastAPI only
```

Ports-and-adapters is what makes `docker|k8s` and `docker|e2b` a config switch
rather than a rewrite. Also: replace the hand-rolled event walk in
`agent_loop.py` with a real middleware (this becomes notebook 02's example).

### Phase 2 · Projects (#5, #6)
`Project` becomes the top-level entity. Created as **"New Project"** with
`name_status="provisional"`. EM gets a `name_project(name, rationale)` tool,
callable once scope is concrete; emits `project.renamed` on the bus so the UI
retitles the chat live. Multiple projects run concurrently, each with its own
workspace, channel and sandbox.

### Phase 3 · Skills and progressive disclosure (#2)
`SkillsMiddleware` over a `skills/` tree. Base skills sourced by every role;
role skills layered on top; `SubAgent.skills` scoping so a Backend pod cannot
see `author-ticket`.

Proposed library (derived from the six existing prompts):
- **base** — `crew-protocol`, `ticket-discipline`, `workspace-conventions`, `escalation`
- **EM** — `triage-founder-request`, `author-ticket`, `name-project`
- **PM** — `scope-and-clarify`, `write-prd`
- **Backend / Frontend** — `implement-backend`, `implement-frontend`
- **QA / Reviewer** — `write-tests`, `review-diff`

Note: the six prompts are **not** duplicates — measured similarity is only
12–22%. The argument for skills is progressive disclosure and per-subagent
scoping, *not* deduplication.

### Phase 4 · Harness adoption (#9)
- `subagents=[CompiledSubAgent(name=…, runnable=SpawnPodRunnable(role))]` —
  spawning becomes native while staying container-per-agent
- `permissions=` replacing the double-enforced spawn caps
- `interrupt_on=` replacing `POST /escalations`
- `checkpointer=` so a paused agent survives its container (impossible today)
- `SummarizationMiddleware` replacing `history[-40:]`
- `RubricMiddleware` as the basis for evals

### Phase 5 · Kubernetes (#1) + sandbox (#10)
Port mind-palace's `control-plane/app/spawner.py` (129 lines: `V1Pod`,
`create_namespaced_pod`, poll `status.phase`, `read_namespaced_pod_log`,
`delete_namespaced_pod`). Caps become label-selector counts, which survive a
control-plane restart. `/workspace` becomes a PVC. **Drop the `docker.sock`
mount** — the sandbox becomes a pod with a `securityContext`.

### Phase 6 · Observability, docs, tests
- Loki + Grafana in compose — **ports 3100 and 3003** (3000/3001 taken)
- Phoenix stays for traces; Grafana is for logs. Different questions.
- Swap the six `logging.basicConfig` calls to `setup_logging()`
- README rewritten from the code; ADRs for the contested calls
- Notebook regression run

---

## 4 · Notebook ladder

**Audience:** already knows LangGraph, state, nodes, edges, tool loops.
Does **not** know harnesses or DeepAgents.

**Thesis:** *A harness makes an operating system intelligent.* The OS has had
every capability for fifty years; what it lacked was judgement. The model has
judgement and no hands. The harness is the wiring. **The container is the
computer.**

Do **not** teach by comparison with LangGraph. Do **not** centre Redis — it is
one transport choice, a footnote in notebook 07.

| # | Notebook | Repo code | Content |
|---|---|---|---|
| 01 | What a harness is | none | Claude Code, Codex, Pi, Hermes, DeepAgents as one family. OS + judgement. Container = computer. **Ends by naming the mechanism**: a harness is a middleware stack — one printed stack, unexplained. Cliffhanger. |
| 02 | A middleware, built by hand | none | The lens. Seven hooks: `before_agent`, `before_model`, `modify_request`, `wrap_model_call`, `wrap_tool_call`, `after_model`, `after_agent`. Write a 10-line one, watch it fire. `create_deep_agent` is an **assembler**. |
| 03 | `FilesystemMiddleware` | `agents/backend/` | `ls, read_file, write_file, edit_file, glob, grep, execute`. The agent acts like an engineer because it has an engineer's tools. **Automatic eviction** above 20k tokens = `command > out.txt` done for you. Plan-before-code, todos in files. |
| 04 | `SkillsMiddleware` | `skills/` | Instructions are files too. `SKILL.md` + YAML frontmatter; metadata at `before_agent`, body via `read_file` on demand. `SubAgent.skills` scoping. |
| 05 | `SubAgentMiddleware` | `adapters/` | Three shapes: `SubAgent` in-process · `CompiledSubAgent` (any Runnable) · `AsyncSubAgent` (remote HTTP). **Ours is the middle one** — the Runnable starts a container/pod. |
| 06 | The control middlewares | `agents/shared/` | `HumanInTheLoopMiddleware`, `_ToolExclusionMiddleware`, `SummarizationMiddleware`, `PatchToolCallsMiddleware`, `RubricMiddleware`. Theme: a harness constrains as much as it enables. |
| 07 | The team harness | `apps/control_plane/` | What DeepAgents does *not* give you and we wrote: projects, roles, caps, escalation, shared workspace, event bus. Six computers, one team. |
| 08 | End to end | all | One founder request through the system, watched in Grafana and Phoenix. |

**Gradient:** 01–02 need no repo at all; by 05–07 the notebooks read our source
line by line. Middleware is at **02, not 5** — it is the organising principle,
and this audience finds it a one-line concept.

Write each notebook **as its phase lands**, so every cell is executable —
matching how notebooks 09–12 work in the Session 6 repo.

---

## 5 · Verified facts about DeepAgents 0.6.8

`create_deep_agent` accepts **18 params**; this repo currently passes 4
(`model`, `tools`, `system_prompt`, `backend`):

```
model, tools, system_prompt, middleware, subagents, skills, memory,
permissions, backend, interrupt_on, response_format, state_schema,
context_schema, checkpointer, store, debug, name, cache
```

**It is a middleware assembler.** Default stack:

```
FilesystemMiddleware      ls, read_file, write_file, edit_file, glob, grep, execute
SkillsMiddleware          progressive disclosure (if skills=)
SubAgentMiddleware        the task tool
AsyncSubAgentMiddleware   remote subagents
MemoryMiddleware          if memory=
HumanInTheLoopMiddleware  if interrupt_on=
PatchToolCallsMiddleware  repairs malformed tool calls
_ToolExclusionMiddleware  enforces a profile's excluded tools
```

Every preloaded tool arrives via a middleware. There is no tool registry.

**Eviction is built in** — `FilesystemMiddleware.__init__`:
```
tool_token_limit_before_evict: int | None = 20000
human_message_token_limit_before_evict: int | None = 50000
```
> "automatically evicts large tool results to the file system when they exceed
> a token threshold, preventing context window saturation."

**Sub-agent shapes:**
```
SubAgent          name, description, system_prompt, tools, model, middleware,
                  interrupt_on, skills, permissions, response_format
CompiledSubAgent  name, description, runnable          ← ours (container/pod)
AsyncSubAgent     name, description, graph_id, url, headers
```

**Skills format** — a directory with `SKILL.md`:
```markdown
---
name: web-research
description: Structured approach to conducting thorough web research
allowed_tools: [read_file, execute]
---
# body loaded on demand
```
Sources load in order, later overriding earlier: base → role → project.

**Version shims already in the code** (keep them): `FilesystemBackend` moved to
top level, and the prompt kwarg was renamed `instructions` → `system_prompt`.

---

## 6 · Prompt caching — why it is dropped

Gemini 3.5 Flash needs **4096 tokens** of stable prefix for implicit caching.
Measured prompt sizes:

```
pm 1471 · em 1366 · reviewer 834 · qa 789 · frontend 745 · backend 715
```

Nothing is close, and `usage_metadata.input_token_details.cache_read` is `0`.
Progressive disclosure makes this *worse*, not better. Revisit only if a shared
base grows past 4096 tokens — and only if that content earns its place.

---

## 7 · Current environment

- **Docker was stopped** at time of writing — 0 containers.
- minikube installed, **stopped**, profile: 4 CPUs / 6144 MB, **driver=docker**
  (so Docker must run first). Context `minikube` is current.
- Ports in use elsewhere: 3000/3002 (frontends), 8000/8001 (backends),
  6007 (Phoenix, shared), 6379/6380/6381 (redis), 9000 (crew control plane),
  4000 (crew web), 9001–9005 (crew MCP).
- Free and reserved for this work: **3003 Grafana, 3100 Loki**.
- `E2B_API_KEY` is in `.env` and verified working, but **no code reads it**.

## 8 · Repo facts worth not rediscovering

- Only the EM has `spawn_agent`, `write_ticket` and `escalate_to_founder` —
  see `ROLE_TOOLS` in `agents/shared/agent_tools.py`. The EM is structurally
  the orchestrator.
- 10 roles are declared in spawn limits; **only 6 have modules**
  (`ROLE_MODULES`): em, pm, backend, frontend, qa, reviewer.
  devops/security/sre/tech_writer are named but unimplemented.
- `mcp-sandbox` mounts `/var/run/docker.sock` — the worst security hole here.
- The `web` service is gated behind a `ui` profile whose comment claims
  `apps/web` is empty. It is **not** — it has source and a built image.
  Un-gated in `docker-compose.override.yml`.
- Prior successful runs left real output in `workspace/task-679e71a1/`
  (56 files: PRD, backend, tests, frontend) — proof the crew works.
