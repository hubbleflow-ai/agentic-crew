"""Builds notebooks/03_the_filesystem_middleware.ipynb."""
import nbformat as nbf
nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"}}
C=[]
def md(t): C.append(nbf.v4.new_markdown_cell(t.strip()))
def code(t): C.append(nbf.v4.new_code_cell(t.strip()))

md("""
# 03 · The filesystem middleware

In notebook 01 we built an agent with no tools and it had nine. Seven of those
came from one layer:

`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`

That is not a random selection. It is, almost exactly, what an engineer does at
a terminal.

This notebook is about why giving a model that particular set changes how it
behaves — and about one feature of this middleware that quietly solves the
hardest problem in agent design.
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
## A filesystem is a place to think

Give a model only a chat history and everything it knows has to fit in its
context window. It re-derives the same conclusions, forgets what it decided
four turns ago, and gets more expensive with every message.

Give it a filesystem and something changes. It can write a plan down, work
through it, and come back. Notes outlive the turn that produced them. And
critically — another agent can read them.

That last point is why the crew shares one volume. A backend engineer does not
message the reviewer a diff. It writes files, and the reviewer reads them.
""")

code("""
from deepagents.backends import FilesystemBackend
import inspect

print(inspect.signature(FilesystemBackend.__init__))
print()
print("Our agents are constructed with:")
src = (ROOT / "agents/shared/agent_loop.py").read_text()
start = src.index("def _filesystem_backend")
print(src[start:src.index("def _skill_sources")].rstrip())
""")

md("""
`virtual_mode=True` is doing real work there. Every path the model uses is
rooted at that directory, so a model that asks for `/etc/passwd` gets
`<workspace>/etc/passwd`. It is not a policy the model is asked to respect; it
is arithmetic on the path before the read happens.

And in the cluster, the volume is mounted with `subPath: <project-id>`, so one
project's `/workspace` is a different directory on disk from another's. Two
layers, both structural.
""")

md("""
## Watch it work

A real agent, a real directory, no instructions about *how* to use files.
""")

code("""
import os, tempfile
from deepagents import create_deep_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage

assert os.environ.get("GOOGLE_API_KEY"), "set GOOGLE_API_KEY (see .env)"

workdir = tempfile.mkdtemp(prefix="nb03-")

agent = create_deep_agent(
    model=ChatGoogleGenerativeAI(model="gemini-3.5-flash"),
    system_prompt="You are a backend engineer. Work in files, not in your reply.",
    backend=FilesystemBackend(root_dir=workdir, virtual_mode=True),
)

result = await agent.ainvoke({"messages": [{"role": "user", "content":
    "Write a Python function `is_healthy(pool)` that returns False when the pool "
    "is exhausted, into src/health.py. Then write one test for it in tests/test_health.py."
}]})

print("Tool calls the model chose to make:")
for m in result["messages"]:
    if isinstance(m, AIMessage):
        for tc in (m.tool_calls or []):
            arg = tc["args"].get("file_path") or tc["args"].get("path") or ""
            print(f"   {tc['name']:<12} {arg}")
""")

code("""
for path in sorted(pathlib.Path(workdir).rglob("*")):
    if path.is_file():
        print("=" * 60)
        print(path.relative_to(workdir))
        print("=" * 60)
        print(path.read_text())
""")

md("""
Nobody told it to create `src/` and `tests/`. It behaves like an engineer
because it was handed an engineer's tools, and the conventions came with them.

This is the cheapest lever in the whole harness. Changing what an agent *is*
mostly means changing what it can touch.
""")

md("""
## The part that matters most: eviction

Here is the problem every agent system hits.

An agent runs `pytest` on a large suite. The output is 40,000 tokens. That
result goes into the message history, and now every subsequent model call
carries it. Two more commands like that and the context window is full of
console output, the useful conversation has been squeezed out, and the agent
starts forgetting what it was doing.

The usual fix is to write `command > out.txt` and read back only what you need.
`FilesystemMiddleware` does that for you.
""")

code("""
from deepagents import FilesystemMiddleware
import inspect

sig = inspect.signature(FilesystemMiddleware.__init__)
for name, param in sig.parameters.items():
    if "token" in name or "evict" in name:
        print(f"  {name} = {param.default}")
""")

md("""
A tool result over `tool_token_limit_before_evict` is **written to the
filesystem and replaced in the history with its path**. The agent sees
something like "output was large, it is at `/tmp/tool-output-3.txt`" and can
`grep` it for the three lines it actually wanted.

`human_message_token_limit_before_evict` does the same for enormous pasted
input.

Two consequences worth stating plainly:

* **A long-running agent stops being a context-window problem.** Its working
  memory is disk; its context holds the pointer.
* **This is why the filesystem is required.** In `deepagents`,
  `FilesystemMiddleware` is not optional — you can replace the backend, not
  remove the layer. Eviction has to have somewhere to put things.
""")

code("""
import deepagents.graph as g
print("Middleware the library will not let you remove:")
for cls, _aliases in g._REQUIRED_MIDDLEWARE:
    print("   ", cls.__name__)
""")

md("""
## What you now know

1. The filesystem tools make an agent behave like an engineer, without being
   told to.
2. `virtual_mode` roots every path structurally; `subPath` does it again at the
   volume. Neither relies on the model cooperating.
3. **Automatic eviction** turns large tool output into a file path, so context
   exhaustion stops being the limiting factor on how long an agent can work.
4. The filesystem layer is required, because eviction depends on it.

Next: instructions are files too. `SkillsMiddleware`, and why the crew's
knowledge is not in its system prompt.
""")

nb.cells=C
nbf.write(nb, "notebooks/03_the_filesystem_middleware.ipynb")
print("wrote 03 ·", len(C), "cells")
