"""Builds notebooks/01_the_deepagents_harness.ipynb. Throwaway."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)",
                              "language": "python", "name": "python3"}}
C = []
def md(t): C.append(nbf.v4.new_markdown_cell(t))
def code(t): C.append(nbf.v4.new_code_cell(t))

md("""# 01 · The DeepAgents Harness

*What you get when the loop is written for you.*

In the Trip Concierge you built the agent loop by hand: a model node, a tool
node, a conditional edge, and a message list threaded through them.

**DeepAgents is that loop, pre-built**, plus three things you would otherwise
write yourself: a planning tool, a filesystem the agent can read and write, and
sub-agent spawning. You supply a system prompt and a list of tools; it returns a
compiled agent.

That shifts where the work goes. In this codebase, an agent is barely any code
at all — the difference between an Engineering Manager and a QA Engineer is
almost entirely **a different markdown file**.

In this notebook:

1. The harness — what `create_deep_agent` is actually given here.
2. Why the filesystem matters, and how it differs from the event bus.
3. A role is a prompt: the six implemented roles, side by side.
4. The tools every agent gets, and the ones it does not.
5. The loop the harness does *not* give you — the part this repo wrote.

> **Prerequisite.** The crew stack: `docker compose up -d`.
> This notebook mostly reads source; notebook 02 drives the system.""")

code('''import os
if os.path.basename(os.getcwd()) == "notebooks":
    os.chdir("..")

import json, re, pathlib, textwrap, httpx

CONTROL_PLANE = os.getenv("CREW_API", "http://localhost:9000")

try:
    h = httpx.get(f"{CONTROL_PLANE}/health", timeout=5).json()
    print(f"control plane up · {len(h['spawn_limits'])} roles declared · "
          f"{h['active_agents']} agents active · {h['active_tasks']} tasks")
except Exception as exc:
    print(f"control plane unreachable at {CONTROL_PLANE}: {type(exc).__name__}")
    print("start it with:  docker compose up -d")''')

md("""## Step 1 — What the harness is given

Every agent container runs exactly one DeepAgents agent. Here is the
construction, from `agents/shared/agent_loop.py`.""")

code('''src = pathlib.Path("agents/shared/agent_loop.py").read_text()

start = src.index("llm = ChatGoogleGenerativeAI")
end = src.index("self.history: list[Any] = []")
print(textwrap.dedent(src[start:end]).strip())''')

md("""Three things are handed over, and that is the whole configuration:

| Argument | What it is |
|---|---|
| `model` | Gemini, constructed directly rather than through a provider string |
| `tools` | the role's own tools — see Step 4 |
| `backend` | a **real directory on disk**, not in-memory state |
| `system_prompt` | the role definition — see Step 3 |

Note the `try/except TypeError` around the final call. The keyword has been
named both `system_prompt` and `instructions` across DeepAgents versions, so the
code tries one and falls back to the other. A small thing, but it is the kind of
compatibility shim you should expect when building on a fast-moving harness.""")

md("""## Step 2 — The filesystem is the shared workspace

`FilesystemBackend(root_dir=..., virtual_mode=True)` gives the agent file tools
whose writes land on a **real, shared volume**. Every agent container mounts the
same `/workspace`, so a file the Backend Engineer writes is genuinely there when
the Reviewer goes looking.

This produces a clean split, and it is the design decision worth taking away.""")

code('''for line in src.splitlines()[:26]:
    if line.strip().startswith("*") or "Data split" in line:
        print(line.rstrip())''')

md("""| | Filesystem | Redis |
|---|---|---|
| Holds | work artifacts, the ticket, notes | the live event stream |
| Lifetime | durable | ephemeral |
| Role | **source of truth** | signalling only |

Redis carries *"the Backend Engineer just called a tool"*. The filesystem holds
the file it wrote. If Redis were wiped, the work would survive; if the workspace
were wiped, the work would be gone.

Previous runs left their output there — this is real code, written by agents:""")

code('''ws = pathlib.Path("workspace")
tasks = sorted(p for p in ws.iterdir() if p.is_dir() and p.name.startswith("task-"))
for t in tasks:
    files = list(t.rglob("*"))
    n = sum(1 for f in files if f.is_file())
    print(f"  {t.name:20} {n:3} files")

biggest = max(tasks, key=lambda t: sum(1 for f in t.rglob('*') if f.is_file()), default=None)
if biggest:
    # skip every dot-directory (.ruff_cache, .pytest_cache, .mypy_cache ...)
    interesting = [f for f in sorted(biggest.rglob("*"))
                   if f.is_file()
                   and not any(part.startswith((".", "__")) for part in f.parts)]
    print(f"\\ninside {biggest.name} · {len(interesting)} source files:")
    for f in interesting[:12]:
        print("   ", f.relative_to(biggest))''')

md("""## Step 3 — A role is a prompt

The control plane declares ten roles. Only six have an agent module behind them
— the others are named but not implemented, which the code says plainly.""")

code('''limits = httpx.get(f"{CONTROL_PLANE}/health", timeout=5).json()["spawn_limits"]

cp = pathlib.Path("apps/control_plane/main.py").read_text()
block = cp[cp.index("ROLE_MODULES"):cp.index("}", cp.index("ROLE_MODULES"))]
modules = dict(re.findall(r'"(\w+)":\s*"(\w+)"', block))   # role -> module dir

print(f"  {'role':22} {'cap':>4}   module")
for role, cap in limits.items():
    mod = f"agents/{modules[role]}/" if role in modules else "— not implemented"
    print(f"  {role:22} {cap:>4}   {mod}")''')

code('''# What actually distinguishes one role from another?
for d in sorted(pathlib.Path("agents").iterdir()):
    p = d / "system_prompt.md"
    m = d / "main.py"
    if p.exists():
        print(f"  {d.name:12} prompt {len(p.read_text().splitlines()):>4} lines"
              f"   main.py {len(m.read_text().splitlines()):>3} lines")''')

md("""That is the point of the harness in one table. Each role's `main.py` is around
twenty lines — it loads a prompt and starts the loop. The behaviour lives in
sixty to a hundred lines of markdown.

Here is what makes an Engineering Manager:""")

code('''em = pathlib.Path("agents/em/system_prompt.md").read_text()
print(em[:1200])''')

md("""## Step 4 — The tools an agent is given

`build_role_tools(ctx)` binds tools with the task and agent already baked in, so
the model never has to pass a `task_id` or guess a ticket id.""")

code('''at = pathlib.Path("agents/shared/agent_tools.py").read_text()
import re
for m in re.finditer(r'    async def (\\w+)\\(([^)]*)\\)[^:]*:\\n        """(.*?)"""', at, re.S):
    name, args, doc = m.group(1), " ".join(m.group(2).split()), " ".join(m.group(3).split())
    print(f"  {name}({args[:52]}{'...' if len(args) > 52 else ''})")
    print(f"      {doc[:110]}\\n")''')

md("""Two details worth pulling out.

**The ticket id is bound, not guessed.** The comment in the source says this
directly — agents guessing ticket ids caused 404s, so the id is derived from the
task and closed over. A recurring lesson: if the model can get an identifier
wrong, do not let it supply one.

**`read_ticket` returns a status, not an error.** If the EM has not authored the
ticket yet, the tool replies `{"status": "no_ticket", ...}` with an explanation.
A 404 would read as a failure the model might retry or panic about; a structured
"not yet, here is what that means" is something it can act on. **Tool errors are
prompt surface** — write them for the reader.

On top of these, DeepAgents supplies its own built-ins: planning (a todo list
the agent keeps for itself) and filesystem tools backed by the workspace above.""")

md("""## Step 5 — What the harness does *not* give you

DeepAgents provides the agent. It does not provide a **team**. Everything that
makes these agents a crew was written in this repo:

- a Redis channel per task, and an envelope format for peer messages;
- the loop that subscribes, converts an envelope into a user message, invokes
  the agent, and republishes what happened;
- spawn caps, so an EM cannot create forty engineers;
- escalation back to a human.

That subscribe-invoke-publish loop is the crew's real contribution:""")

code('''for line in src.splitlines()[11:24]:
    print(line.rstrip())''')

md("""Read step 2d closely: after each invocation the loop **walks the new messages**
and publishes reasoning, tool calls, usage and the response onto the bus. That is
what makes the run watchable — it is the same idea as mapping
`astream_events` into a frontend protocol in the Trip Concierge, done at the
level of a whole team instead of one agent.""")

md("""## Recap

1. **DeepAgents supplies the loop**, plus planning, filesystem and sub-agent
   tools. You supply a model, tools and a prompt.
2. **The backend is a real shared directory.** Filesystem is the source of
   truth; Redis carries only the live event stream.
3. **A role is a system prompt.** Six implemented roles, each ~20 lines of
   Python and ~60–110 lines of markdown. Four more are declared but have no
   module.
4. **Tools are bound with context** — task and ticket ids are closed over, never
   passed by the model.
5. **Error text is prompt text.** `read_ticket` returning `no_ticket` with an
   explanation beats a 404.
6. **The harness gives you an agent; the crew is yours to build** — channels,
   envelopes, spawn caps and escalation all live in this repo.

Notebook 02 starts the crew and watches these agents work.

---

### Exercises

1. Add a seventh role — a `tech_writer` module. How much Python, and how much
   markdown?
2. Compare `agents/qa/system_prompt.md` with `agents/reviewer/system_prompt.md`.
   Both inspect work; what makes them behave differently?
3. Delete a file from a previous task's workspace and re-run that task. Does the
   agent notice?
4. `virtual_mode=True` on the backend — find what it changes, and what would
   break with it off.""")

nb.cells = C
nbf.write(nb, "notebooks/01_the_deepagents_harness.ipynb")
print(f"wrote notebooks/01_the_deepagents_harness.ipynb ({len(C)} cells)")
