"""Builds notebooks/05_subagents_are_pods.ipynb."""
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
# 05 · Sub-agents, and why ours are pods

One of the nine tools in notebook 01 was `task`. That is `SubAgentMiddleware`,
and it is how an agent hands work to another agent.

The interesting question is not *whether* a harness can do this — they all can.
It is **where the sub-agent runs**, because that decides everything about what
it can do and what it costs.
""")

code("""
import pathlib, sys
ROOT = pathlib.Path.cwd()
while not (ROOT / "pyproject.toml").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
print("repo root:", ROOT)
""")

md("""
## Three shapes

`deepagents` gives you three, and they are genuinely different things.
""")

code("""
import inspect
from deepagents import AsyncSubAgent, CompiledSubAgent, SubAgent

for cls in (SubAgent, CompiledSubAgent, AsyncSubAgent):
    print("=" * 60)
    print(cls.__name__)
    keys = getattr(cls, "__annotations__", None) or {
        f.name: f.type for f in getattr(cls, "__dataclass_fields__", {}).values()}
    for k, v in keys.items():
        print(f"    {k}: {getattr(v, '__name__', v)}")
""")

md("""
| Shape | Runs | Isolation | Use when |
|---|---|---|---|
| `SubAgent` | in your process | none — same memory, same context budget | a quick focused sub-task |
| `CompiledSubAgent` | wherever its Runnable runs | whatever you build | you want to decide |
| `AsyncSubAgent` | another service, over HTTP | separate process, separate machine | the sub-agent is already deployed |

`CompiledSubAgent` is the interesting one. It takes **any Runnable**. That is a
very wide door: a Runnable can be "make an API call, wait, return the result",
and the API call can be to Kubernetes.

That is how a sub-agent becomes a pod.
""")

md("""
## What the crew actually does

We do not use the `task` tool for teammates. The EM calls `spawn_agent`, which
goes to the control plane, which creates a **Job**.

The distinction is not pedantic — it changes four things.
""")

code("""
src = (ROOT / "apps/control_plane/adapters/k8s_runtime.py").read_text()
start = src.index("    async def launch")
print(src[start:src.index("    async def status")].rstrip())
""")

md("""
Compare an in-process `SubAgent` with that:

|  | in-process sub-agent | our Job |
|---|---|---|
| context window | shares the parent's | its own, fresh |
| crash | takes the parent with it | retried by the cluster, parent unaffected |
| resources | whatever the parent has | requests and limits it declares |
| lifetime | the parent's turn | outlives the process that asked for it |

That last row is the one that matters most here. **The control plane can
restart and the agents keep working**, because nothing about their lifecycle
lives in its memory.
""")

md("""
## Counting from the cluster, not from memory

Which leads to the design decision this repo is most opinionated about.

If you cap how many backend engineers a project may have, you have to count
them somewhere. The obvious place is a dictionary in the control plane. That
dictionary is wrong the moment the process restarts — it says zero, and the
founder can spawn straight past the ceiling while eight agents are running.

So the count is a question asked of the cluster.
""")

code("""
start = src.index("    async def _live_jobs")
print(src[start:src.index("    async def logs")].rstrip())
""")

md("""
A label selector over live Jobs. There is nothing to get out of step, because
there is no second copy of the number.

The rules themselves are separate, and have no idea Kubernetes exists.
""")

code("""
from apps.control_plane.domain.caps import (
    GLOBAL_LIMITS, PER_PROJECT_LIMITS, Census, check_spawn,
)

role = list(PER_PROJECT_LIMITS)[2]
print(f"role: {role}")
print(f"  per-project limit: {PER_PROJECT_LIMITS[role]}")
print(f"  cluster-wide limit: {GLOBAL_LIMITS[role]}")
print()

for in_project, everywhere, override in [(3, 5, False), (4, 5, False), (4, 5, True), (0, 12, True)]:
    refusal = check_spawn(Census(role, in_project, everywhere), override=override)
    verdict = "allowed" if refusal is None else f"refused ({refusal.scope})"
    print(f"  in_project={in_project:<2} everywhere={everywhere:<3} override={str(override):<5} -> {verdict}")
""")

md("""
Two ceilings, and only one of them can be overridden.

The per-project limit is a **quality** limit — five backend engineers on one
project usually means the work was not decomposed, and a founder who insists
can have their way.

The cluster-wide limit is a **resource** limit. No amount of founder approval
creates more cluster, so `override` does not touch it.

Notice also that the refusal is written for a reader:
""")

code("""
refusal = check_spawn(Census(role, 4, 5))
print(refusal.message)
""")

md("""
"Do not retry this spawn" is there because the reader is a language model. A
bare 409 invites a retry loop against a limit that will not move.

## And a third layer, which does not trust our code at all

The caps are Python. Python has bugs. So the cluster has its own limit,
enforced at admission by the API server.
""")

code("""
print((ROOT / "deploy/02-quota.yaml").read_text())
""")

md("""
## What you now know

1. Sub-agents come in three shapes; `CompiledSubAgent` takes any Runnable,
   which is the door through which "a sub-agent is a pod" walks.
2. A Job gets its own context window, its own resources, and a lifetime
   independent of the process that asked for it.
3. Spawn caps are counted **from the cluster**, so they survive a restart.
4. Three layers: pure rules in `domain/`, a live count from the API server, and
   a ResourceQuota that does not depend on our code being correct.

Next: the middlewares that take capability *away*.
""")

nb.cells=C
nbf.write(nb, "notebooks/05_subagents_are_pods.ipynb")
print("wrote 05 ·", len(C), "cells")
