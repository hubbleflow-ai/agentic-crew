"""Builds notebooks/02_running_the_crew.ipynb. Throwaway."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)",
                              "language": "python", "name": "python3"}}
C = []
def md(t): C.append(nbf.v4.new_markdown_cell(t))
def code(t): C.append(nbf.v4.new_code_cell(t))

md("""# 02 · Running the Crew

*One request in. A team assembles itself.*

Notebook 01 read the harness. This one drives it: we post a request to the
control plane and watch an Engineering Manager appear, decide who it needs, and
spawn them.

The thing to watch for is that **nobody schedules this**. There is no workflow
engine and no plan file. An agent reads its brief, decides a Product Manager is
required, and calls an API to create one.

In this notebook:

1. The control plane's surface — what can be asked of it.
2. Post a request, and watch the EM spawn a team.
3. Read the live event stream over Redis.
4. Find what the crew wrote to the shared workspace.
5. Spawn caps, and why they exist.

> **Prerequisite.** `docker compose up -d`. Agents call Gemini, so a run takes
> a few minutes and costs tokens.""")

code('''import os
if os.path.basename(os.getcwd()) == "notebooks":
    os.chdir("..")

import json, time, pathlib, subprocess, httpx

CREW = os.getenv("CREW_API", "http://localhost:9000")

health = httpx.get(f"{CREW}/health", timeout=5).json()
print(f"control plane · {health['active_agents']} agents · {health['active_tasks']} tasks")
print("roles it can spawn:", ", ".join(health["spawn_limits"]))''')

md("""## Step 1 — The control plane's surface

The crew is driven entirely over HTTP. These are the routes that matter.""")

code('''cp = pathlib.Path("apps/control_plane/main.py").read_text()
for line in cp.splitlines():
    s = line.strip()
    if s.startswith("@app.") and "on_event" not in s:
        print("   ", s.replace("@app.", "").replace('("', "  ").replace('")', ""))''')

md("""Two are worth noting before we start.

`POST /tasks` takes a field called **`request`**, not `brief` — the founder's
natural-language input. And `POST /escalations` exists because an agent that
cannot decide something should ask a human rather than guess.""")

md("""## Step 2 — Post a request

One HTTP call. No agent is named, no plan is supplied.""")

code('''REQUEST = ("Add a GET /healthz endpoint to the backend that returns "
           '{"status":"ok"} plus the app version, with one test for it.')

task = httpx.post(f"{CREW}/tasks", timeout=60, json={
    "request": REQUEST,
    "scenario": "build",
    "local_hour": 14,
}).json()

TASK_ID = task["task_id"]
print(json.dumps(task, indent=2))''')

md("""Look at the response. The task came back already `em_running`, with an
`em_agent_id` attached.

The control plane did that on its own: creating a task **auto-spawns an
Engineering Manager**, because a request with nobody to read it is useless. Every
other agent is spawned later by the EM, not by us.""")

md("""## Step 3 — Watch the team assemble

Each agent is its own container. Poll `/agents` and `docker ps` and you can see
the team appear.""")

code('''def team(task_id: str | None = None) -> list[dict]:
    """Agents the control plane knows about, optionally only this task's."""
    roster = httpx.get(f"{CREW}/agents", timeout=10).json()
    if task_id:
        roster = [a for a in roster if a.get("task_id") == task_id]
    return roster

def containers() -> list[str]:
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                         capture_output=True, text=True).stdout
    return sorted(n for n in out.split() if n.startswith("crew-"))

start = time.time()
deadline = start + 240
seen: set[str] = set()

print("agents on THIS task as they appear:\\n")
while time.time() < deadline:
    roster = team(TASK_ID)
    for a in roster:
        key = f"{a['role']}·{a['agent_id']}"
        if key not in seen:
            seen.add(key)
            print(f"  +{time.time() - start:5.0f}s   {a['role']:22} {a['agent_id']}")
    if len(roster) >= 2:
        break
    time.sleep(10)

print(f"\\nthis task's team: {len(team(TASK_ID))}   ·   all agents alive: {len(team())}")
print("containers:", containers())''')

md("""The Engineering Manager was created by the control plane. Anything after it
was created **by the EM**, mid-reasoning, by calling `POST /agents/spawn`.

You can see that decision in its log — a Gemini call, then a spawn:""")

code('''em_container = next((c for c in containers() if "engineering" in c), None)
if em_container:
    logs = subprocess.run(["docker", "logs", "--tail", "60", em_container],
                          capture_output=True, text=True).stdout
    for line in logs.splitlines():
        if "agents/spawn" in line or "deepagent.ready" in line or "event.published type=response" in line:
            print("   ", line.strip()[:130])
else:
    print("no EM container found")''')

md("""## Step 4 — The live event stream

Agents publish to a Redis channel per task: `crew/task/<id>/messages`. The
control plane relays it to the browser over `WS /stream/{task_id}`; the UI on
**localhost:4000** renders it.

We can subscribe to the same channel directly.""")

code('''import asyncio, redis.asyncio as aioredis

async def watch(task_id: str, seconds: int = 45) -> list[dict]:
    r = aioredis.from_url(os.getenv("CREW_REDIS", "redis://localhost:6381/0"))
    ps = r.pubsub()
    await ps.subscribe(f"crew/task/{task_id}/messages")
    events, end = [], time.time() + seconds
    while time.time() < end:
        m = await ps.get_message(ignore_subscribe_messages=True, timeout=5.0)
        if m and m.get("data"):
            try:
                events.append(json.loads(m["data"]))
            except Exception:
                pass
    await ps.unsubscribe(); await r.aclose()
    return events

events = await watch(TASK_ID)
print(f"{len(events)} events in 45s\\n")

from collections import Counter
for kind, n in Counter(e.get("type", "?") for e in events).most_common():
    print(f"  {n:4}  {kind}")''')

code('''# what the agents were actually doing
def who(e):
    return str(e.get("from", "?")).split("/")[0]

for e in events:
    kind, p = e.get("type"), e.get("payload", {})
    if kind == "tool_call":
        preview = str(p.get("result_preview", ""))[:70].replace("\\n", " ")
        arrow = f"  ->  {preview}" if preview else ""
        print(f"  {who(e):22} {p.get('tool','?'):22}{arrow}")
    elif kind == "reasoning":
        print(f"  {who(e):22} thinks: {str(p.get('text',''))[:90]}")
    elif kind == "response":
        print(f"  {who(e):22} says  : {str(p.get('text',''))[:90]}")''')

md("""That stream is the whole observability story. Every reasoning step, tool call
and reply from every agent lands on one channel, tagged with who sent it. The UI
is a renderer over exactly this.

Note it is **ephemeral** — Redis pub/sub, not a log. Miss it and it is gone,
which is why the durable work goes to the filesystem instead.""")

md("""## Step 5 — What the crew produced

The event stream says what happened. The workspace holds what was made.""")

code('''ws = pathlib.Path("workspace") / TASK_ID
if ws.exists():
    files = [f for f in sorted(ws.rglob("*"))
             if f.is_file() and not any(p.startswith((".", "__")) for p in f.parts)]
    print(f"{len(files)} file(s) in {ws}:")
    for f in files[:15]:
        print(f"   {f.relative_to(ws)}  ({f.stat().st_size} bytes)")
    if files:
        first = files[0]
        print(f"\\n─── {first.relative_to(ws)} ───")
        print(first.read_text()[:600])
else:
    print(f"{ws} not created yet — agents may still be scoping.")
    print("Re-run this cell in a minute, or watch localhost:4000.")''')

md("""If the directory is still empty, that is not a failure — the EM and PM spend
their first minutes on scope and a ticket before anyone writes code. Earlier
tasks in `workspace/` show the finished shape: a PRD, a backend, tests, a
frontend.""")

md("""## Step 6 — Spawn caps

An agent that can create agents can create too many. The control plane caps each
role.""")

code('''limits = httpx.get(f"{CREW}/health", timeout=5).json()["spawn_limits"]
for role, cap in limits.items():
    print(f"  {role:22} max {cap}")

print("\\nasking for one more EM than allowed:")
resp = httpx.post(f"{CREW}/agents/spawn", timeout=30, json={
    "task_id": TASK_ID, "role": "engineering_manager", "requested_by": "notebook",
})
print(f"   HTTP {resp.status_code} · {str(resp.text)[:160]}")''')

md("""The cap is enforced in the control plane, not requested in a prompt — the same
principle as the payment gate in the Trip Concierge. **If a limit matters, put it
where the model cannot reach it.**

`override_cap` exists on the spawn request, and requires founder approval. That
is the escape hatch, and it is deliberately a human decision.""")

md("""## Recap

1. **One request creates a team.** `POST /tasks` auto-spawns an Engineering
   Manager; the EM spawns everyone else by calling the same API you just did.
2. **Agents are containers.** `docker ps` shows the team as it grows and shrinks.
3. **The event stream is one Redis channel per task**, relayed to the UI over a
   WebSocket. Ephemeral by design.
4. **The workspace is the durable output** — PRD, source, tests — shared across
   every agent on the task.
5. **Caps live in the control plane**, not the prompt, and lifting one needs a
   human.

Notebook 03 looks at where that code actually runs, and how well it is contained.

---

### Exercises

1. Post a deliberately vague request — `"hi"`. Read the EM's system prompt first
   and predict what it does. Does it spawn anyone?
2. Watch `/agents` while a task finishes. Do agent containers stop, or linger?
3. Send a second task while the first is running. Do they share a workspace, a
   channel, or agents?
4. `POST /escalations` with a question. Where does it surface, and what is an
   agent supposed to do while it waits?""")

nb.cells = C
nbf.write(nb, "notebooks/02_running_the_crew.ipynb")
print(f"wrote notebooks/02_running_the_crew.ipynb ({len(C)} cells)")
