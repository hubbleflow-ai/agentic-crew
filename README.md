# Agentic Crew

An autonomous engineering team. A founder describes what they want; an
Engineering Manager scopes it, names the project, and pulls in whoever it
needs. Every agent runs in its own Kubernetes pod.

It is also the worked example for a single idea:

> **An agent is a model plus a harness.** The model supplies judgement. The
> operating system has supplied capability for fifty years. A harness is the
> wiring between them — and it is built out of middleware.

If you want the idea before the code, start at
[`notebooks/01_what_a_harness_is.ipynb`](notebooks/).

---

## Running it

```bash
cp .env.example .env          # add GOOGLE_API_KEY
minikube start
./scripts/deploy.sh
```

Then, in another terminal:

```bash
kubectl port-forward -n crew svc/control-plane 8000:8000
```

```bash
curl -X POST localhost:8000/projects \
     -H 'content-type: application/json' \
     -d '{"request":"Add a /healthz endpoint to the payments service."}'
```

```json
{"id": "proj-5fcc0a58", "name": "New Project", "status": "running", "is_named": false}
```

The project is called **New Project** because nobody knows yet what it is. A
minute later the EM has read the request and renamed it:

```bash
curl -s localhost:8000/projects/proj-5fcc0a58
```

```json
{"name": "Payments Service Health Check Endpoint", "is_named": true}
```

Nothing in this repository chose that name. The EM did, by calling a tool.

### Watching it

| | | |
|---|---|---|
| what the crew **did** | `GET /projects/{id}/events` | tool calls, messages, spawns |
| what the processes **logged** | Grafana, `svc/grafana` :3000 | filtered by project and role |
| what the **model** saw | Phoenix, `svc/phoenix` :6006 | prompts, completions, tokens |

The third is usually where a bug turns out to be. The model almost always did
something reasonable given what it was shown; the mistake is in what it was
shown.

### Stopping

```bash
./scripts/teardown.sh          # deletes the namespace, and with it everything
```

Every other day-to-day command — logs, shells, Redis, restarts, what to do when
a pod will not start — is in [Operations](#operations) below.

---

## How a request becomes a running agent

```
POST /projects
      │
      ▼
CrewService.open_project ──────► domain/caps.py     "may another EM start?"
      │                                 ▲
      │                                 │ census: a label selector over live Jobs
      ▼                                 │
KubernetesAgentRuntime.launch ──────────┘
      │
      ▼
   one Job created ─────► and the control plane stops being responsible
                                │
                                ▼
                   kubelet runs `python -m agents.main`
                                │
                     AGENT_ROLE ──► which system prompt, which tools, which skills
                     PROJECT_ID ──► which Redis channel, which workspace
                     ASSIGNMENT ──► the first thing it works on
```

Two details in there are load-bearing.

**The count comes from the cluster.** A label selector over live Jobs, asked
fresh each time. The previous implementation kept a dictionary in the control
plane, which said zero after every restart and let the founder spawn straight
past the ceiling while eight agents were running.

**The assignment travels as an environment variable**, not as a published
message. A pod that finishes booting three seconds after the message went out
would otherwise wait forever for something that already happened.

---

## Layout

```
contracts/          the wire format · owned by neither side
  events.py           Event, EventKind, the channel name
  agent_env.py        which env vars a Job sets and an agent reads

apps/control_plane/
  domain/             rules. no I/O whatsoever
  ports/              contracts. what is needed, never how
  adapters/           I/O. no rules
  service.py          use cases · the only layer that knows both
  api/                HTTP. no decisions

agents/
  main.py             the entry point every container runs
  shared/
    agent_loop.py     the harness: create_deep_agent + the bus
    telemetry.py      middleware · what makes a pod's thinking visible
    agent_tools.py    per-role tool catalogues
  em/ pm/ backend/ …  a system_prompt.md each. that is the whole difference

skills/             instructions loaded on demand
  base/               every role
  roles/<role>/       layered on top

mocks/              MCP services · tickets, github, sandbox, browser, jira
deploy/             every Kubernetes manifest
notebooks/          the teaching ladder, 01 → 08
```

### Why `contracts/` is separate

Because it drifted. The launcher set `AGENT_ROLE`/`PROJECT_ID` while the agent
read `CREW_ROLE`/`CREW_TASK_ID`. Agents published `{from, type}` onto
`crew/task/<id>/messages`; the control plane read `{source, kind}` from
`crew/project/<id>/events`.

Nothing failed loudly. The events simply went nowhere. One definition now,
imported by both sides, so a rename that misses one side does not compile.

### Why `domain/` has no imports

So the rules can be tested with nothing running:

```
$ pytest tests/ -q
55 passed in 0.4s
```

That covers the spawn caps, the naming rules, the refusal messages and the
whole HTTP surface — faster than a container can start, because
`FakeAgentRuntime` satisfies the same port as the Kubernetes one and nothing
above the port can tell them apart.

---

## The five things worth knowing

### 1 · A role is a prompt and a tool catalogue

The Engineering Manager and the QA Engineer run identical code. There is one
`agents/main.py`. What differs is `system_prompt.md`, which skills the role can
see, and which tools are bound in.

`spawn_agent` and `name_project` appear in exactly one catalogue. A backend
engineer cannot build itself a team, and no prompt wording can change that,
because the function was never bound. **Absence beats instruction.**

### 2 · Caps have three layers

| | enforced by | overridable |
|---|---|---|
| 4 backend engineers per project | `domain/caps.py` | yes, by the founder |
| 12 cluster-wide | `domain/caps.py` | no — approval does not create cluster |
| the ResourceQuota | the API server, at admission | no, and it does not trust our code |

The refusal is written for a language model to read:

> This project already has 4 of 4 backend_engineer agents. Give the work to one
> of them, or split it differently. **Do not retry this spawn** — ask the
> founder to approve an override if you genuinely need another.

A bare 409 invites a retry loop against a limit that will not move.

### 3 · Skills cost their description line

A system prompt is paid for on every model call. A skill is paid for only when
it is opened. `SkillsMiddleware` injects the frontmatter — name, description,
path — and the body is fetched with `read_file` if and only if the model
decides it is relevant.

Measured, given six skills and one ambiguous request: **one file opened.**

Which skills a role can see is decided by which directories are in its source
list. A backend engineer's list never contains the EM's, so it cannot read the
scoping playbook.

### 4 · The container is the computer

Whatever you want an agent to be able to do, you arrange by choosing the
computer it gets.

The sandbox is the sharpest case. `sandbox_exec` runs each command as its own
Job: no service-account token, read-only root filesystem, every capability
dropped, a hard deadline, and the project's workspace as the only thing it can
see. The command is `shlex.split`, never handed to a shell, so
`pytest; rm -rf /` is a file-not-found for an oddly named test.

It used to drive a container through the host's `docker.sock`. Beyond not
working in a cluster, that hands the service running model-written code control
of the machine.

### 5 · Escalation does not block

`interrupt_on` suspends the graph until a human answers. Correct for one agent
with someone watching; a deadlock for six pods at 2am.

So `escalate_to_founder` publishes the question, tells the agent to proceed on
its best judgement and record the assumption, and lets a real answer arrive
later as an ordinary message. Liveness over strict gating — a deliberate trade,
and one parameter away from the other choice.

---

## The notebooks

Written for someone who knows LangGraph and has not met a harness. They do not
teach by comparison.

| | | needs |
|---|---|---|
| 01 | What a harness is | the library |
| 02 | The filesystem middleware | the library |
| 03 | A middleware, built by hand | the library |
| 04 | Skills, and paying only for what you read | the library |
| 05 | Sub-agents, and why ours are pods | the repo |
| 06 | The middlewares that take things away | the repo |
| 07 | The team harness | the repo |
| 08 | End to end | a running cluster |

Every cell is executed against real infrastructure before it is committed; the
outputs you see are the outputs it produced.

---

## Configuration

`.env`, never committed:

```
GOOGLE_API_KEY=...        # required
LANGSMITH_API_KEY=...     # optional second trace sink
E2B_API_KEY=...           # optional, unused by default
```

`./scripts/k8s-secrets.sh` pushes those into the cluster as `crew-secrets`.
Non-secret settings — model name, service URLs — are in `deploy/04-config.yaml`
and can be changed without rebuilding an image.

> `deploy/secrets.example.yaml.txt` has a `.txt` extension deliberately. As a
> `.yaml` it was picked up by `kubectl apply -f deploy/` and quietly overwrote
> the real Secret with `replace-me`, which fails much later and somewhere else.


---

## Operations

Everything the crew runs in lives in one namespace, `crew`, on one minikube
node. That is the whole reason the commands below are short: there is nothing
to address but `-n crew`.

Assumes `kubectl` and `minikube` on PATH, and the repo root as the working
directory.

### Start it

```bash
cp .env.example .env               # GOOGLE_API_KEY is the only required key
minikube start                     # docker driver · Docker must be up first
./scripts/deploy.sh
```

`deploy.sh` is safe to re-run and does five things in order:

1. **Guards** — refuses to continue unless minikube is installed and running.
2. **Builds two images into minikube's own daemon** (`eval $(minikube
   docker-env)`, so nothing is pushed to a registry): `crew-base:dev` and
   `crew-browser:dev`. `crew-base:dev` is the control plane, four of the five
   MCP mocks, **and every agent** — `CREW_AGENT_IMAGE` points at it — so a code
   change reaches the agents through this one build.
3. **`kubectl apply -f deploy/`** — namespace, RBAC, quota, the two PVCs,
   ConfigMap, Redis, MCP services, control plane, Loki/Grafana/Phoenix.
4. **`./scripts/k8s-secrets.sh`** — reads `.env` and writes the `crew-secrets`
   Secret. Run it alone after changing a key.
5. **`kubectl rollout restart -n crew deployment`** — needed because `kubectl
   apply` will not restart a pod whose image *tag* did not change, and these
   tags never change.

Expect ten pods: `control-plane`, `redis`, five `mcp-*`, `loki`, `grafana`,
`phoenix`.

```bash
kubectl get pods -n crew
kubectl rollout status -n crew deployment/control-plane --timeout=180s
kubectl get deploy,svc,pvc -n crew
```

### Reach it

Nothing is exposed outside the cluster. Port-forward what you want, each in its
own terminal:

| what | command | then |
|---|---|---|
| the API | `kubectl port-forward -n crew svc/control-plane 8000:8000` | `localhost:8000/docs` |
| logs | `kubectl port-forward -n crew svc/grafana 3000:3000` | `localhost:3000` |
| model traces | `kubectl port-forward -n crew svc/phoenix 6006:6006` | `localhost:6006` |
| raw log queries | `kubectl port-forward -n crew svc/loki 3100:3100` | LogQL over HTTP |
| the store | `kubectl port-forward -n crew svc/redis 6379:6379` | `redis-cli` from the host |

### Drive it

```bash
curl -X POST localhost:8000/projects -H 'content-type: application/json' \
     -d '{"request":"Add a /healthz endpoint to the payments service."}'

curl -s localhost:8000/projects                          # all open projects
curl -s localhost:8000/projects/proj-5fcc0a58            # one, with its name
curl -s 'localhost:8000/projects/proj-5fcc0a58/events?limit=50'
curl -s localhost:8000/projects/proj-5fcc0a58/agents     # who is on it, live
curl -X POST localhost:8000/projects/proj-5fcc0a58/messages \
     -H 'content-type: application/json' -d '{"text":"make it idempotent"}'
curl -X DELETE localhost:8000/projects/proj-5fcc0a58     # close it
```

`ws://localhost:8000/projects/{id}/stream` is the same events, live. The
history endpoint is what a founder sees on reopening; the socket is what they
see while watching.

### Watch the crew work

Agents are **Jobs**, not Deployments — one Job per agent, named
`<role>-<project-id>-<suffix>`. Sandboxes are Jobs too, named
`sbx-<project>-<hex>`.

```bash
kubectl get jobs -n crew -w
kubectl get pods -n crew -w
kubectl get jobs -n crew -l crew.hubbleflow.ai/project=proj-5fcc0a58
```

Three labels are on everything the control plane creates, and they are what
make the commands below work:

```
crew.hubbleflow.ai/managed-by = control-plane
crew.hubbleflow.ai/role       = engineering_manager | product_manager |
                                backend_engineer | frontend_engineer |
                                qa_engineer | code_reviewer
crew.hubbleflow.ai/project    = proj-<id>
```

### Logs

**One agent**, followed live:

```bash
kubectl logs -n crew job/backend-engineer-proj-5fcc0a58-3f2a1b -f
kubectl logs -n crew <pod-name> --tail=100 -f
kubectl logs -n crew <pod-name> --previous      # the crash before this one
```

**Everyone on a project**, or everyone in a role:

```bash
kubectl logs -n crew -l crew.hubbleflow.ai/project=proj-5fcc0a58 \
        --tail=50 --prefix --max-log-requests=20
kubectl logs -n crew -l crew.hubbleflow.ai/role=backend_engineer --prefix
```

**The control plane** — spawns, refusals, recorder failures:

```bash
kubectl logs -n crew deploy/control-plane -f
```

Finished Jobs are deleted by the cluster **five minutes** after they end
(`TTL_AFTER_FINISHED_S`), so `kubectl logs` is a live tool with a short memory.
The durable copy is Loki.

**Grafana** (`localhost:3000`, anonymous admin, dashboard *Crew · what the
agents are doing*). Every process pushes structured JSON straight to Loki —
there is no log shipper and no `docker.sock`. Four labels are indexed, kept
deliberately low-cardinality:

```logql
{project_id="proj-5fcc0a58"}                    # one project, everything
{role="backend_engineer", level="ERROR"}        # one role, only failures
{service="control-plane"} |= "spawn.refused"    # why a cap said no
{project_id="proj-5fcc0a58"} | json | line_format "{{.message}}"
```

Or query Loki directly, no browser:

```bash
curl -sG localhost:3100/loki/api/v1/query_range \
     --data-urlencode 'query={project_id="proj-5fcc0a58"}' \
     --data-urlencode 'limit=100' | jq -r '.data.result[].values[][1]'
```

**Phoenix** (`localhost:6006`) is the fourth place, and usually the right one:
prompts, completions, token counts, every MCP hop. The model almost always did
something reasonable given what it was shown — the bug is generally in what it
was shown.

### Get a shell inside a container

```bash
kubectl exec -it -n crew deploy/control-plane -- bash
kubectl exec -it -n crew deploy/mcp-sandbox   -- bash
kubectl exec -it -n crew deploy/redis         -- sh     # alpine · no bash
```

Agents are Jobs, so address the pod, not a Deployment:

```bash
POD=$(kubectl get pods -n crew -l crew.hubbleflow.ai/role=engineering_manager \
      -o name | head -1)
kubectl exec -it -n crew "$POD" -- bash
kubectl exec -n crew "$POD" -- ls -la /workspace/workspace
```

The image is `python:3.12-slim` plus `curl`, `git` and `ripgrep`, so `bash`
is there and `pip` works. A finished agent's pod is gone within five minutes —
there is nothing to exec into after that.

**Copy work off the volume before it disappears:**

```bash
mkdir -p ./rescued
kubectl exec -n crew "$POD" -- \
  tar cf - -C /workspace/workspace --exclude='.*_cache' --exclude='__pycache__' . \
  | tar xf - -C ./rescued
```

### Inspect the store

Projects, their names and their transcripts live in Redis (see
`adapters/redis_store.py`). It is readable while the crew is running:

```bash
R="kubectl exec -n crew deploy/redis -- redis-cli"
$R --scan --pattern 'crew:*'                        # not KEYS · same result, no stall
$R GET   crew:project:proj-5fcc0a58                 # one project, as JSON
$R ZRANGE crew:projects:active 0 -1 WITHSCORES      # open projects, score = created_at
$R LRANGE crew:project:proj-5fcc0a58:history -5 -1  # the last five events
$R INFO persistence | grep -E 'aof_enabled|rdb_last_bgsave_status'
```

Proving it survives a restart — the reason the store moved out of memory:

```bash
kubectl delete pod -n crew -l app=control-plane      # the control plane dies
kubectl rollout status -n crew deployment/control-plane
curl -s localhost:8000/projects                      # same projects, same names
```

### Restart and redeploy

```bash
./scripts/deploy.sh                                   # after any code change
kubectl rollout restart -n crew deployment/control-plane
kubectl rollout restart -n crew deployment            # all of them
kubectl rollout status  -n crew deployment/control-plane
```

Redis uses `strategy: Recreate`, not RollingUpdate — its volume is
ReadWriteOnce, and a rolling update would leave the new pod waiting on a volume
the old one never releases. The cost is a few seconds with no event bus:
restart Redis and any agent mid-turn loses live delivery, though the project
itself is on disk.

Changing the model for the whole crew needs no rebuild:

```bash
kubectl edit configmap crew-config -n crew            # GEMINI_MODEL, LOG_LEVEL
kubectl rollout restart -n crew deployment
```

### Stop work without deleting anything

```bash
kubectl delete jobs -n crew -l crew.hubbleflow.ai/project=proj-5fcc0a58   # one project
kubectl delete jobs -n crew -l crew.hubbleflow.ai/managed-by=control-plane # every agent
```

Infrastructure, both volumes and every transcript survive this. It is the right
way to stop a runaway crew.

### Tear down

```bash
./scripts/teardown.sh     # deletes namespace 'crew' · both PVCs go with it
minikube stop             # keeps everything, stops the VM
minikube delete           # nothing left, images included
```

`teardown.sh` is a delete, not a restart: `redis-data` takes the projects and
transcripts, `crew-workspace` takes every file the agents wrote. Copy anything
you want with the `tar` command above **first**.

### When something is wrong

| symptom | what it usually is | look here |
|---|---|---|
| pods stuck `Pending` | the ResourceQuota, not the node | `kubectl describe quota -n crew` · `kubectl get events -n crew --sort-by=.lastTimestamp` |
| `ErrImagePull` on `crew-base:dev` | image built into the *host* daemon, not minikube's | `eval $(minikube docker-env) && docker images \| grep crew` |
| agent Job `Failed` after three tries | `BackoffLimitExceeded` · `backoffLimit: 2` | `kubectl logs -n crew <pod> --previous` |
| agent looks `Running` but does nothing | a pod that cannot start still reads as active to a Job — this is why `AgentStatus` carries `is_stuck` | `kubectl describe pod -n crew <pod>` · Phoenix |
| redis pod `ContainerCreating` forever | RWO volume still held by the old pod | check `strategy: Recreate` in `deploy/10-redis.yaml` |
| control plane `CrashLoopBackOff` | missing Secret, or Redis unreachable | `kubectl logs -n crew deploy/control-plane --previous` |
| `spawn refused` in the UI | a cap said no, on purpose | `kubectl logs -n crew deploy/control-plane \| grep spawn.refused` |
| `kubectl`: TLS handshake timeout | minikube busy or paused | `minikube status`, then retry |
| projects empty after a restart | Redis running without its AOF | `kubectl exec -n crew deploy/redis -- redis-cli INFO persistence` |

---

## Known limits

* **Redis is a single node with no replication.** It holds both the live bus
  and the project store, with an append-only file on a PVC, so a restarted or
  rescheduled pod comes back to the same projects. Lose the *volume* and the
  transcripts go with it. `everysec` fsync means a hard kill can drop up to a
  second of events; the work itself is on the workspace volume.
* **`./scripts/teardown.sh` deletes the namespace**, and the PVCs with it.
  Tearing down is not a restart — it is a delete.
* **Grafana has anonymous admin access.** It is a teaching cluster on a laptop.
* **The MCP services are mocks.** GitHub, JIRA and tickets are stand-ins with
  the same interface, so the lesson does not depend on anyone holding a token.
  The sandbox and browser are real.
