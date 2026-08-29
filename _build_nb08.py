"""Builds notebooks/08_end_to_end.ipynb."""
import ast

import nbformat as nbf
nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"}}
C=[]
def md(t): C.append(nbf.v4.new_markdown_cell(t.strip()))
def code(t):
    # A cell that cannot parse is a bug in *this* script, not in the notebook.
    # Usually a lone \\n inside the triple-quoted source, which Python turns into
    # a real newline and splits the generated line in half.
    source = t.strip()
    ast.parse(source)
    C.append(nbf.v4.new_code_cell(source))

md("""
# 08 · End to end

One founder request, on a real cluster, watched all the way through.

Everything in the previous seven notebooks appears here at least once:
middleware publishing telemetry, skills opening on demand, a Job being created,
caps counted from label selectors, and the recorder writing it all down.

> **Prerequisites.** `minikube start`, then `./scripts/deploy.sh`, then in a
> separate terminal:
>
> ```bash
> kubectl port-forward -n crew svc/control-plane 8000:8000
> kubectl port-forward -n crew svc/grafana 3000:3000
> kubectl port-forward -n crew svc/phoenix 6006:6006
> ```
""")

code("""
import pathlib, sys
ROOT = pathlib.Path.cwd()
while not (ROOT / "pyproject.toml").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

import httpx
CP = "http://localhost:8000"

health = httpx.get(f"{CP}/health", timeout=5).json()
print("control plane:", health)
""")

md("""
## Before: what is running
""")

code("""
import subprocess

def kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True).stdout

print(kubectl("get", "pods", "-n", "crew", "--no-headers",
              "-o", "custom-columns=NAME:.metadata.name,STATUS:.status.phase"))
""")

md("""
Eleven platform pods and no agents. Agents do not exist until somebody asks for
something.

## The request
""")

code("""
REQUEST = (
    "Add a /healthz endpoint to the payments service. It should report whether "
    "the database is reachable, and return 503 if it is not. Keep the scope small."
)

project = httpx.post(f"{CP}/projects", json={"request": REQUEST}, timeout=30).json()
PROJECT_ID = project["id"]

for k, v in project.items():
    print(f"  {k:<12} {v}")
""")

md("""
`"New Project"`, `is_named: false`. The founder's chat has a title that is
honest about not knowing yet.

## What that turned into
""")

code("""
print(kubectl("get", "jobs", "-n", "crew",
              "-l", f"crew.hubbleflow.ai/project={PROJECT_ID}"))
""")

code("""
import json

raw = kubectl("get", "job", "-n", "crew",
              "-l", f"crew.hubbleflow.ai/project={PROJECT_ID}", "-o", "json")
job = json.loads(raw)["items"][0]
container = job["spec"]["template"]["spec"]["containers"][0]

print("labels:")
for k, v in job["metadata"]["labels"].items():
    print(f"    {k} = {v}")
print("\\nenvironment handed to the container:")
for e in container["env"]:
    print(f"    {e['name']:<14} {e['value'][:70]}")
""")

md("""
The founder's sentence is sitting in `ASSIGNMENT`.

It travels as an environment variable rather than as a published message on
purpose: a pod that finishes booting three seconds after the message went out
would otherwise miss it and wait forever for something that already happened.

## Watching it work
""")

code("""
import time

deadline = time.time() + 300
seen = 0
while time.time() < deadline:
    events = httpx.get(f"{CP}/projects/{PROJECT_ID}/events", params={"limit": 500}, timeout=15).json()
    if len(events) > seen:
        seen = len(events)
        print(f"\\r  events: {seen}", end="", flush=True)
    named = httpx.get(f"{CP}/projects/{PROJECT_ID}", timeout=10).json()["is_named"]
    if named and seen > 12:
        break
    time.sleep(5)
print()
""")

code("""
events = httpx.get(f"{CP}/projects/{PROJECT_ID}/events", params={"limit": 500}, timeout=15).json()

for e in events[:40]:
    p = e["payload"]
    if e["kind"] == "usage":
        detail = f"in={p.get('input_tokens')} out={p.get('output_tokens')}"
    else:
        detail = p.get("text") or p.get("tool") or p.get("role") or p.get("name") or ""
    print(f"  {e['kind']:<16} {e['source'][:24]:<24} {str(detail)[:56]}")
""")

md("""
Read that trace against the earlier notebooks:

* `agent_ready` — the harness booted and announced its tool catalogue (06)
* `tool_call` pairs — `TelemetryMiddleware.awrap_tool_call` (02)
* `read_file` on a `SKILL.md` — progressive disclosure (04)
* `glob`, `ls`, `write_file` — `FilesystemMiddleware` (03)
* `usage` — token spend, per turn
* `agent_spawned` — a second Job (05)
* `project_renamed` — the EM's own decision (07)
""")

md("""
## The project now
""")

code("""
project = httpx.get(f"{CP}/projects/{PROJECT_ID}", timeout=10).json()
for k, v in project.items():
    print(f"  {k:<12} {v}")

print("\\nroster:")
for a in httpx.get(f"{CP}/projects/{PROJECT_ID}/agents", timeout=15).json():
    print(f"  {a['role']:<22} {a['state']:<10} {a['reason']}")
""")

md("""
The name was not assigned by any code in this repository. The EM read the
request, decided what was being built, and called `name_project`.

## The caps, against a live cluster
""")

code("""
for i in range(6):
    r = httpx.post(f"{CP}/projects/{PROJECT_ID}/agents",
                   json={"role": "backend_engineer", "assignment": f"slice {i+1}"},
                   timeout=30)
    if r.status_code == 201:
        print(f"  spawn {i+1}: created {r.json()['agent']}")
    else:
        d = r.json()["detail"]
        print(f"  spawn {i+1}: {r.status_code} [{d['scope']}] {d['current']}/{d['limit']}")
        print(f"           {d['message'][:88]}...")
""")

md("""
Four allowed, then refused — and the refusal is counted from Jobs the API
server can see, not from anything held in the control plane's memory.

Confirm that independently:
""")

code("""
print(kubectl("get", "jobs", "-n", "crew",
              "-l", f"crew.hubbleflow.ai/project={PROJECT_ID},crew.hubbleflow.ai/role=backend_engineer",
              "--no-headers", "-o", "custom-columns=NAME:.metadata.name,ACTIVE:.status.active"))
""")

md("""
## The logs

Everything every pod wrote, labelled by project, role, service and level.
""")

code("""
LOKI = "http://localhost:3100"   # kubectl port-forward -n crew svc/loki 3100:3100

try:
    r = httpx.get(f"{LOKI}/loki/api/v1/query_range",
                  params={"query": '{project_id="%s"}' % PROJECT_ID, "limit": 300},
                  timeout=15).json()
    streams = r["data"]["result"]
    print(f"streams: {len(streams)}   lines: {sum(len(s['values']) for s in streams)}\\n")
    for s in streams:
        lbl = s["stream"]
        print(f"  role={lbl.get('role','-'):<20} service={lbl.get('service','-'):<14} "
              f"level={lbl.get('level','-'):<7} lines={len(s['values'])}")
except Exception as e:
    print("port-forward loki to query it directly:", e)
    print("  kubectl port-forward -n crew svc/loki 3100:3100")
""")

md("""
## Where to look with your eyes

**Grafana** — <http://localhost:3000> → Dashboards → *Crew · what the agents are
doing*. Filter by project and role. The datasource and dashboard are
provisioned, so there is nothing to configure.

**Phoenix** — <http://localhost:6006>. Every Gemini call: the prompt that went
in, the completion that came back, the tools offered, tokens and latency.

The division is worth internalising:

| | answers |
|---|---|
| the event stream | what the crew *did* |
| Loki / Grafana | what the processes *logged* |
| Phoenix | what the *model* saw and said |

When something goes wrong, the third one is usually where the answer is — the
model almost always did something reasonable given what it was actually shown,
and the bug is in what it was shown.
""")

md("""
## Cleaning up
""")

code("""
r = httpx.delete(f"{CP}/projects/{PROJECT_ID}", timeout=15).json()
print("project:", r["status"])
print(subprocess.run(
    ["kubectl", "delete", "jobs", "-n", "crew",
     "-l", f"crew.hubbleflow.ai/project={PROJECT_ID}"],
    capture_output=True, text=True).stdout)
""")

md("""
## The whole thing, in one paragraph

An operating system could always do everything and never knew what was worth
doing. A model knows what is worth doing and has no hands. A harness is the
wiring, and it is built from middleware: layers that add capability, and layers
that take it away.

Scale that to a team and the wiring grows a second storey — projects, caps, a
shared volume, a bus, a record — but the shape does not change. And because the
capability an agent has *is* the computer you give it, the interesting
decisions end up being about pods, volumes and service accounts.

**The container is the computer. The prompt is who is sitting at it.**
""")

nb.cells=C
nbf.write(nb, "notebooks/08_end_to_end.ipynb")
print("wrote 08 ·", len(C), "cells")
