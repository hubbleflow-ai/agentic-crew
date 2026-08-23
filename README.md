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
| 02 | A middleware, built by hand | the library |
| 03 | The filesystem middleware | the library |
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

## Known limits

* **Project state is in memory.** A control-plane restart forgets project names
  and history. Agents keep running — their lifecycle is the cluster's — and
  `InMemoryProjectStore` is one port implementation away from Postgres.
* **Redis is disposable.** No persistence, no replication. An in-flight turn
  dies with it; the work is on the volume.
* **Grafana has anonymous admin access.** It is a teaching cluster on a laptop.
* **The MCP services are mocks.** GitHub, JIRA and tickets are stand-ins with
  the same interface, so the lesson does not depend on anyone holding a token.
  The sandbox and browser are real.
