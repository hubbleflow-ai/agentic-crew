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

| Kubernetes | **FULL** — everything in the cluster | decided 2026-08-22; see below |

### Kubernetes: full cluster — DECIDED

Everything runs in Kubernetes. Docker Compose is retired for this repo.

**Namespace:** `hubbleflow-crew`

**Long-lived workloads** — Deployment + Service each:
`control-plane`, `web`, `redis`, `postgres`, `grafana`, `loki`,
and the five MCP servers (`tickets`, `github`, `sandbox`, `browser`, `jira`).

**Dynamic workloads — Jobs, not bare Pods.** An agent is a one-shot process,
so the control plane creates a **Job** via `BatchV1Api.create_namespaced_job`
and then does nothing further. Kubernetes owns the whole lifecycle:

```yaml
kind: Job
spec:
  backoffLimit: 2                 # k8s retries a crashed agent
  ttlSecondsAfterFinished: 300    # the TTL controller deletes it afterwards
  template:
    metadata:
      labels: {app: crew-agent, role: <role>, project_id: <id>}
    spec:
      restartPolicy: Never
```

We deliberately do **not** poll `status.phase` or call `delete_namespaced_pod`
the way mind-palace does — that is us doing lifecycle management in a
Kubernetes costume. With a Job, placement, retry, completion tracking and
cleanup are the cluster's responsibility. The control plane's only act is to
*ask* for an agent; everything after the request belongs to Kubernetes.

Watch completion with a label-selector watch on Jobs, not a polling loop.

**Why full pays for itself** — each of these replaces something we would
otherwise hand-roll:

| Requirement | Kubernetes gives us |
|---|---|
| #4 caps per agent type | label-selector counts + `ResourceQuota` — survives a control-plane restart, unlike today's in-memory dict |
| #6 concurrent projects | scheduler handles placement; quota bounds the whole namespace |
| #10 sandbox isolation | `securityContext` (non-root, `readOnlyRootFilesystem`, dropped caps) — and **no `docker.sock` anywhere** |
| #1 container per agent | a Pod *is* the container, with resource limits attached |

**Shared workspace:** a PVC mounted at `/workspace` by every agent pod.
minikube's default `standard` StorageClass is hostPath and supports
`ReadWriteMany` on a single node, which is all we need.

**Access:** `kubectl port-forward` for development; keep the same host ports as
now so nothing else in the plan changes — web 4000, control-plane 9000,
Grafana 3003, Loki 3100.

**Images:** built locally then `minikube image load crew-agent:latest` (and
`crew-sandbox`, `agentic-crew-web`). This is the step people trip over — a pod
stuck in `ErrImagePull` almost always means the image was never loaded.
Set `imagePullPolicy: IfNotPresent` so the cluster never reaches for a registry.

**Cost accepted:** the cohort inspects with `kubectl get pods`, not `docker ps`.
minikube must be running (profile: 4 CPUs / 6144 MB, **driver=docker**, so
Docker still has to be up underneath).

**Layout:**
```
deploy/
  namespace.yaml
  base/            redis, postgres, loki, grafana, mcp-*, control-plane, web
  agent-pod.yaml   the template the spawner fills in
  workspace-pvc.yaml
  quota.yaml       ResourceQuota — the global cap for #4/#6
```

---

## 2 · The ten requirements

| # | Requirement | Status today |
|---|---|---|
| 1 | Each agent in its own container; 3 backend engineers = 3 containers | ✅ already works |
| 2 | Progressive disclosure of skills | ❌ full prompt every call |
| 3 | EM/PM decide who answers a generic question | ⚠️ EM triages alone; PM never sees founder |
| 4 | Max caps per agent type | ✅ per-project + global, counted from live Jobs |
| 5 | Chat named "New Project" until scope is concrete, then renamed | ✅ domain + API; EM tool still to wire |
| 6 | Multiple concurrent projects | ✅ 3 concurrent, isolated workspaces, global caps |
| 7 | Extremely clean code, proper patterns | ⚠️ new code clean (0 errors, 55 tests); `main.py` still there |
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

### Phase 1 · Layering and middleware — **DONE** (`cf90c4d`, `f22b8d6`)
Split `apps/control_plane/main.py` (544 lines, mixes HTTP + orchestration +
Docker) into:

```
domain/     the rules, as plain Python with no I/O whatsoever
            Project, Task, Agent, Ticket · "a project starts as 'New Project'"
            · "a cap of 4 is exceeded at the 5th" · unit-testable in ms,
            with no cluster running

ports/      one-line contracts saying WHAT is needed, never HOW
            class AgentRuntime(Protocol):
                async def launch(self, role, project_id) -> AgentHandle: ...
                async def status(self, handle) -> AgentStatus: ...
            also SandboxBackend, EventBus

adapters/   the implementations of those contracts
            k8s_runtime (Jobs) · fake_runtime (tests) · redis_events
            memory_store · e2b_sandbox (later)

api/        FastAPI routes and nothing else — they call domain functions
            and never touch Kubernetes
```

**Why this earns its keep here:** the cap logic (#4) and project-naming rules
(#5) are the parts most likely to carry bugs, and today neither can be tested
without Docker running. After the split they can. Swapping Kubernetes for
Docker, or adding E2B for sandboxes, becomes a config line rather than an edit
to business logic. And notebook 07 points at these files to explain what a
*team* harness is — "here is the rule, in one file, with no infrastructure in
it" teaches far better than a 544-line module.

Ports-and-adapters is what makes `docker|k8s` and `docker|e2b` a config switch
rather than a rewrite. Also: replace the hand-rolled event walk in
`agent_loop.py` with a real middleware (this becomes notebook 02's example).

**What landed, and what changed from this sketch:**

- A fourth layer, `service.py`, sits between `api/` and `domain/`. Routes
  turned out to need somewhere to compose *several* port calls (count the
  census, ask the domain, launch, emit) and putting that in a route puts
  policy back in FastAPI. Ports-and-adapters calls this the application layer.
- **No `docker_runtime`.** "Kubernetes full" means a Docker fallback would be
  a second lifecycle implementation to keep correct, so the second adapter is
  `fake_runtime` — in-memory, for tests. Stated here because the sketch above
  promised one.
- `AgentRuntime` gained `handles()` alongside `census()`: the roster the
  founder sees needs *who*, not just *how many*.
- `AgentStatus` carries a `reason` and an `is_stuck` flag. Verified against
  minikube: a pod that cannot start (missing Secret, unpullable image) reads
  as *active* to a Job, so a Job-only status calls a permanently broken agent
  "running". Kubernetes also never fails those on its own.
- A crashed pod is **not** terminal while the Job has retries left. Only the
  Job's `Failed` condition ends an agent.
- Store is `InMemoryProjectStore` for now. The Postgres adapter is a port
  implementation away; nothing above it changes.

**Verified on the cluster, not just typed** (`kubectl` transcript in commit
`cf90c4d`): healthy agent → `succeeded`; failing agent → 3 attempts →
`BackoffLimitExceeded`; bad image → flagged stuck at `ErrImagePull`.

### Phase 2 · Projects (#5, #6) — **MOSTLY DONE** (`f22b8d6`)

Done: `Project` domain type, `/projects` API replacing `/tasks`, three
concurrent projects, `PROVISIONAL_NAME`, rename with validation, per-project
event history and live stream.

Still to do: give the EM a `name_project` tool so the rename is *its* decision
rather than an endpoint nobody calls, and move the agent-side callers off
`/tasks` so `main.py` can be deleted.

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

### Phase 5 · Kubernetes, full (#1) + sandbox (#10)
Write `deploy/` (see §1). Implement `adapters/k8s_runtime.py` behind the `AgentRuntime` port. Reference
mind-palace's `control-plane/app/spawner.py` for the client mechanics, but
**not** its design — it creates bare Pods and hand-manages their lifecycle.
We create Jobs and let the cluster do it.

Caps become label-selector counts over Jobs plus a namespace `ResourceQuota`.
`/workspace` becomes a PVC. **The `docker.sock` mount disappears entirely** —
the sandbox is a pod with a `securityContext`, so nothing needs the host
daemon.

Keep `adapters/docker_runtime.py` working: it is the fallback when no cluster
is available, and the `AgentRuntime` port is what makes that a config switch.

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
