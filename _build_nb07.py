"""Builds notebooks/07_the_team_harness.ipynb."""
import nbformat as nbf
nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"}}
C=[]
def md(t): C.append(nbf.v4.new_markdown_cell(t.strip()))
def code(t): C.append(nbf.v4.new_code_cell(t.strip()))

md("""
# 07 · The team harness

Everything so far was about one agent: what it can touch, what it knows, what
it may not do.

This notebook is about the layer above. DeepAgents gives you an excellent
harness for **an** agent. It has nothing to say about six of them working on
the same thing at once.

That gap is what `apps/control_plane/` fills, and the shape of the answer is
worth studying because it is the same shape as a middleware stack — just one
level up.
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
## What is missing when you have six

| Question | DeepAgents | Here |
|---|---|---|
| What are we working on? | — | `domain/project.py` |
| How many of each role? | — | `domain/caps.py` |
| Who decides? | — | the EM's tool catalogue |
| Where does work live? | one agent's filesystem | one volume, `subPath` per project |
| How do they hear each other? | — | one Redis channel per project |
| What happened? | — | the recorder + Loki |

None of that is a criticism. A harness for one agent should not have opinions
about org charts.
""")

md("""
## Four layers, one job each

The whole control plane is arranged so that each file has exactly one kind of
thing in it.
""")

code("""
for layer, blurb in [
    ("domain",   "rules. no I/O at all"),
    ("ports",    "contracts. what is needed, never how"),
    ("adapters", "I/O. no rules"),
    ("api",      "HTTP. no decisions"),
]:
    files = sorted(p.name for p in (ROOT / "apps/control_plane" / layer).glob("*.py")
                   if p.name != "__init__.py")
    print(f"  {layer:<10} {blurb:<32} {', '.join(files)}")
print(f"  {'service.py':<10} {'use cases · the only layer that knows both'}")
""")

md("""
The test for whether this is real: can the rules be exercised with no
infrastructure?
""")

code("""
import subprocess, sys, time

start = time.perf_counter()
out = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
                     cwd=ROOT, capture_output=True, text=True)
elapsed = time.perf_counter() - start

print(out.stdout.strip().splitlines()[-1])
print(f"in {elapsed:.1f}s — no cluster, no Redis, no Docker")
""")

md("""
That is the entire argument for the split. Those tests cover the spawn caps,
the naming rules, the refusal messages and the whole HTTP surface, and they run
faster than a container can start.

## Ports: the seam

A port is a contract with no implementation. Here is the one that decides where
agents run.
""")

code("""
src = (ROOT / "apps/control_plane/ports/runtime.py").read_text()
start = src.index("class AgentRuntime")
print(src[start:].rstrip())
""")

md("""
Two implementations satisfy it. One creates Kubernetes Jobs. One is a
dictionary.
""")

code("""
from apps.control_plane.adapters.fake_runtime import FakeAgentRuntime
from apps.control_plane.adapters.k8s_runtime import KubernetesAgentRuntime
from apps.control_plane.ports.runtime import AgentRuntime

for impl in (KubernetesAgentRuntime, FakeAgentRuntime):
    print(f"  {impl.__name__:<26} satisfies AgentRuntime: {isinstance(impl(), AgentRuntime)}")
""")

md("""
Nothing above the port can tell them apart, which is why the tests above are
possible at all.

## A rule, with no infrastructure in it

`domain/project.py` is the whole of "a chat is called New Project until the EM
knows what it is".
""")

code("""
from apps.control_plane.domain.project import PROVISIONAL_NAME, NamingError, Project

p = Project(id="proj-demo")
print(f"  new project        name={p.name!r}  is_named={p.is_named}")

named = p.rename("  Rate   limiting for the public API ")
print(f"  after rename       name={named.name!r}  is_named={named.is_named}")

for bad in ("", "New Project", "x" * 80):
    try:
        p.rename(bad)
    except NamingError as e:
        print(f"  rejected {bad[:14]!r:<18} {e}")

print(f"\\n  original unchanged: {p.name!r}   (frozen · transitions return new instances)")
""")

md("""
No database, no cluster, no mock. A rule you can read in one file and check in
one line.

## Who decides who answers

The founder talks to two roles. Which one handles a given message is the EM's
call, and that decision is not in code — it is in a skill.
""")

code("""
skill = (ROOT / "skills/roles/engineering-manager/scoping-a-request/SKILL.md").read_text()
start = skill.index("## Who answers?")
print(skill[start:skill.index("## Spawning")].rstrip())
""")

md("""
This is a judgement call with no clean rule behind it, so it belongs in
instructions the model reads, not in a branch. The things with clean rules —
how many agents, what a project may be called — are in `domain/`.

That division is the useful one: **rules in code, judgement in skills.**
""")

md("""
## Recording what happened

One subtlety that cost a debugging session and is worth repeating.

Agents publish events from inside their own pods. They cannot reach the control
plane's store. So if every publisher also recorded, a project's history would
contain only what the control plane itself said — every tool call and every
agent message would be lost.
""")

code("""
src = (ROOT / "apps/control_plane/service.py").read_text()
start = src.index("    async def _emit")
print(src[start:].rstrip())
""")

md("""
One subscriber, on a pattern that matches every project, writing everything
down. Single writer, and it sees both sides.

## What you now know

1. DeepAgents harnesses one agent; a *team* needs projects, caps, a shared
   workspace, a bus, and a record. That is `apps/control_plane/`.
2. Four layers, one concern each. The proof it is real: the rules test in under
   a second with nothing running.
3. A port has two implementations — Kubernetes and a dictionary — and nothing
   above it can tell which is in use.
4. Rules in code, judgement in skills.
5. One recorder writes history, because the publishers cannot.

Next: all of it at once, on a real cluster.
""")

nb.cells=C
nbf.write(nb, "notebooks/07_the_team_harness.ipynb")
print("wrote 07 ·", len(C), "cells")
