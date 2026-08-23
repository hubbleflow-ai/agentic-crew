"""Builds notebooks/06_middlewares_that_constrain.ipynb."""
import nbformat as nbf
nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"}}
C=[]
def md(t): C.append(nbf.v4.new_markdown_cell(t.strip()))
def code(t): C.append(nbf.v4.new_code_cell(t.strip()))

md("""
# 06 · The middlewares that take things away

Every layer so far added capability. Filesystem, skills, sub-agents.

This notebook is about the other half, and it is the half that decides whether
you can put the thing in front of a customer.

> A harness constrains as much as it enables.

The useful framing: a middleware sits between the model and the world in
**both** directions. Everything that lets a model reach further can also be
used to stop it short.
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
## What ships with the library
""")

code("""
import langchain.agents.middleware as M

for name in sorted(n for n in dir(M) if n.endswith("Middleware")):
    doc = (getattr(M, name).__doc__ or "").strip().split("\\n")[0]
    print(f"  {name:<34} {doc[:56]}")
""")

md("""
Four of those are worth knowing by name.

**`HumanInTheLoopMiddleware`** — pauses before a named tool and waits for a
person. Configured through `interrupt_on`.

**`SummarizationMiddleware`** — compresses old turns when the conversation gets
long. Different from filesystem eviction: eviction moves a single large *tool
result* out; summarisation compresses *conversation history*.

**`ModelCallLimitMiddleware`** — a hard ceiling on model calls per run. This is
the one that stops a loop from becoming a bill.

**`PIIDetectionMiddleware`** — redacts or blocks on detected personal data,
which is a policy you want enforced mechanically rather than by prompt.
""")

md("""
## Human in the loop, in five lines

`interrupt_on` names the tools that require approval.
""")

code("""
import inspect
from deepagents import create_deep_agent

params = inspect.signature(create_deep_agent).parameters
for name in ("interrupt_on", "permissions", "checkpointer"):
    print(f"  {name:<14} {params[name].default!r}")

print()
print("Shape:")
print('  interrupt_on={"execute": True}                # pause before every execute')
print('  interrupt_on={"delete": {"allow_accept": True, "allow_edit": True}}')
""")

md("""
An interrupt needs a `checkpointer`, and that is not an implementation detail —
it is the whole idea. Pausing for a human means the agent's state has to
**survive the pause**. Without somewhere to persist it, there is nothing to
resume into.

Which is why "ask a human" and "be restartable" are the same feature.
""")

md("""
## What the crew does instead, and why

We do not use `interrupt_on` for founder escalation, and the reason is worth
being explicit about.

`interrupt_on` blocks. It suspends the graph until someone answers. For a
single agent with a person watching, that is correct. For six agents in six
pods with nobody watching at 2am, it is a deadlock — five agents idle behind
one that is waiting for an answer that is not coming.
""")

code("""
src = (ROOT / "apps/control_plane/api/routes.py").read_text()
start = src.index('@router.post("/projects/{project_id}/escalations"')
print(src[start:src.index('@router.get("/projects/{project_id}/events"')].rstrip())
""")

md("""
So escalation here is a **publish**, not a block. The question is surfaced to
the founder, the agent is told to proceed on its best judgement and record the
assumption, and a real answer — if it comes — arrives later as an ordinary
message on the project.

That is a deliberate trade: liveness over strict gating. It is the right
default for a headless crew and the wrong one for, say, a deployment tool. The
mechanism to switch is one parameter.
""")

md("""
## Taking tools away

The most effective constraint in this repo is not a middleware at all. It is
the tool catalogue.
""")

code("""
from agents.shared.agent_tools import ROLE_TOOLS

for role, tools in ROLE_TOOLS.items():
    print(f"  {role:<22} {len(tools)}  {', '.join(tools)}")
""")

md("""
`spawn_agent` appears once. `name_project` appears once. Both belong to the
Engineering Manager.

A backend engineer cannot build itself a team, and no wording in its prompt can
change that, because the function was never bound into its agent. This is the
same principle as the skill sources in notebook 04: **absence beats
instruction**.

## The layers, honestly counted

For "can a backend engineer spawn ten more engineers", the answer is no, four
times over:

1. The tool is not in its catalogue.
2. If it somehow called the endpoint, `check_spawn` refuses past four.
3. If the caps had a bug, the ResourceQuota refuses at admission.
4. If all of that failed, the Job's own `activeDeadlineSeconds` bounds it.

None of those four is a prompt.
""")

md("""
## And the one that is not optional

`_ToolExclusionMiddleware` — the library's own mechanism for removing tools it
installed. Worth knowing it exists, because it means "which tools does this
agent have" is itself a middleware decision, resolved at assembly time.
""")

code("""
import deepagents.graph as g

print("Required (cannot be removed):")
for cls, _ in g._REQUIRED_MIDDLEWARE:
    print("   ", cls.__name__)
print()
print("Tool exclusion is how the library removes what it installed --")
print("so even 'has no filesystem tools' is a middleware outcome.")
""")

md("""
## What you now know

1. A harness constrains as much as it enables; the same hook that adds
   capability can remove it.
2. `interrupt_on` blocks and needs a checkpointer — pausing for a human and
   being restartable are one feature.
3. This crew escalates **without** blocking, on purpose: six pods and no human
   watching makes a blocking gate a deadlock.
4. The strongest constraint here is the tool catalogue. Absence beats
   instruction.

Next: everything DeepAgents does *not* give you, which is what
`apps/control_plane/` is for.
""")

nb.cells=C
nbf.write(nb, "notebooks/06_middlewares_that_constrain.ipynb")
print("wrote 06 ·", len(C), "cells")
