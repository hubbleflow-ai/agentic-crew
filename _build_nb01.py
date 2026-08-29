"""Builds notebooks/01_what_a_harness_is.ipynb."""
import ast

import nbformat as nbf

nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)",
                              "language": "python", "name": "python3"}}
C = []
def md(t): C.append(nbf.v4.new_markdown_cell(t.strip()))
def code(t):
    # A cell that cannot parse is a bug in *this* script, not in the notebook.
    # Usually a lone \\n inside the triple-quoted source, which Python turns into
    # a real newline and splits the generated line in half.
    source = t.strip()
    ast.parse(source)
    C.append(nbf.v4.new_code_cell(source))

md("""
# 01 · What a harness is

Claude Code. Codex. Pi. Hermes. DeepAgents.

These are not five products that happen to resemble each other. They are five
instances of one thing, and that thing has a name: a **harness**.

By the end of this notebook you will be able to say what a harness is in one
sentence, and you will have watched one read your code and explain it back to
you.
""")

md("""
## The two halves

Your operating system has been able to do almost everything for fifty years.
It reads and writes files. It runs processes, opens sockets, spawns children,
enforces permissions, isolates users. There is very little a competent engineer
does at a keyboard that the OS cannot do.

What it never had was **judgement**. It will happily run `rm -rf /` because you
typed it. It has no opinion about whether that was a good idea.

A language model is the mirror image. It has judgement — it can read a stack
trace and tell you which of four things probably caused it — and it has no
hands at all. It cannot open a file. It cannot run a test. It produces text.

> **A harness is the wiring between judgement and capability.**
>
> It gives the model hands, and it gives the operating system judgement.

Neither half is new. The wiring is.
""")

md("""
## Why this makes the container the interesting unit

Once you see it that way, one consequence follows immediately.

If the harness's job is to expose an operating system to a model, then whatever
you want the agent to be able to do, you arrange by choosing **which operating
system it gets**.

Give it a container with `pytest` installed and it can run tests. Give it a
container with no network route out and it cannot exfiltrate anything, no
matter what it decides to do. Give it a read-only root filesystem and it cannot
persist a change you did not intend.

You are not writing guardrails in the prompt. You are choosing a computer.

> **The container is the computer.** The prompt is who is sitting at it.

This is why the rest of this repo spends so much of its time on pods,
volumes and service accounts. Those are not deployment details bolted on at the
end — they are how you decide what the agent is capable of.
""")

md("""
## What you need to run this

**A Python environment with the project's dependencies.** From the repo root:

```bash
uv venv && uv pip install -r requirements.txt
```

**A `GOOGLE_API_KEY`** in `.env` at the repo root. This notebook makes one real
model call at the end, because a harness that is only described is not
convincing.

Notebooks 01–04 need nothing else. From 05 onward you will want a cluster:

```bash
minikube start && ./scripts/deploy.sh
```
""")

code('''
import os
import pathlib
import warnings

warnings.filterwarnings("ignore")  # the Gemini client is chatty about internals

REPO = pathlib.Path.cwd().parent            # notebooks/ -> repo root

# Read .env without a dependency · one key, one line each.
for line in (REPO / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

assert os.environ.get("GOOGLE_API_KEY"), "no GOOGLE_API_KEY · see .env.example"
print("model:", os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"))
''')

md("""
## The prompt is a file

Before we build anything: **where does the agent's character come from?**

Not from a string buried in a function call. From a file you can read, review
and put under version control — the same idea as a `CLAUDE.md` sitting in a
repository root. Ours is `notebooks/AGENT.md`.

This is not a notebook convenience. Every role in this repo is defined this way:
`agents/backend/system_prompt.md`, `agents/em/system_prompt.md`, and so on. A
role is a file.
""")

code('''
AGENT_MD = (pathlib.Path.cwd() / "AGENT.md").read_text()

print(AGENT_MD[:480], "...\\n")
print(f"({len(AGENT_MD)} characters · this becomes the system prompt verbatim)")
''')

md("""
## Build one

A model, and that file. Nothing else — no tools, no configuration.

Then ask what we got.
""")

code('''
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_google_genai import ChatGoogleGenerativeAI

model = ChatGoogleGenerativeAI(
    model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"), temperature=0
)

agent = create_deep_agent(
    model=model,
    system_prompt=AGENT_MD,                      # the file, passed straight in
    backend=FilesystemBackend(root_dir=REPO),    # the computer it gets
)

tool_node = agent.nodes["tools"]
tools = sorted(getattr(tool_node, "bound", tool_node).tools_by_name)

print(f"We asked for no tools. The agent has {len(tools)}:")
print()
for name in tools:
    print("   ", name)
''')

md("""
We passed a model and a markdown file. We got back a thing that can list a
directory, read a file, edit a file, glob, grep, delete, run a command
(`execute`), and hand work to another agent (`task`).

That is not a small default. That is most of what a junior engineer does at a
terminal, and **none of it was requested**.

A list of tool names proves nothing, though. Before explaining where they came
from, give it a real question about code it has never seen — and watch which of
them it reaches for.
""")

md("""
## Watch it work

`FilesystemBackend(root_dir=REPO)` is the computer we handed it: this
repository, and nothing else.
""")

code('''
question = (
    "Read apps/control_plane/domain/project.py and explain in four sentences "
    "what it does and why it is written that way."
)

result = agent.invoke({"messages": [{"role": "user", "content": question}]})

print("What it actually did:\\n")
for message in result["messages"]:
    for call in getattr(message, "tool_calls", []) or []:
        print(f"  {call['name']}({str(call['args'])[:88]})")

answer = result["messages"][-1].content
if isinstance(answer, list):  # Gemini returns content blocks
    answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))
print("\\nWhat it said:\\n")
print(answer)
''')

md("""
Look at the trace, not just the answer.

It reached for `read_file`. When the path it guessed did not exist it did not
give up and did not invent an explanation — it called `glob` to find the file,
then read it in chunks. Nobody wrote that recovery. It falls out of having
hands and being able to see the result of using them.

And the explanation is about *our* code — the frozen dataclass, the no-I/O rule
in `domain/` — because it read the file. The tools are not decorative.
""")

md("""
## Where the tools came from

`create_deep_agent` does not build an agent from scratch. It calls LangChain's
ordinary `create_agent` — the one you already know — and hands it a **list of
layers**.

The compiled agent does not keep that list, so we watch the call being made.
""")

code('''
import langchain.agents as la
import deepagents.graph as dg
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

captured = {}
real_create_agent = la.create_agent

def spy(*args, **kwargs):
    captured["middleware"] = kwargs.get("middleware")
    return real_create_agent(*args, **kwargs)

la.create_agent = dg.create_agent = spy
try:
    # A fake model · this build is about structure, and costs nothing.
    create_deep_agent(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="ok")]),
        system_prompt=AGENT_MD,
    )
finally:
    la.create_agent = dg.create_agent = real_create_agent

print("create_deep_agent installed these layers:\\n")
for layer in captured["middleware"]:
    names = [getattr(t, "name", str(t)) for t in getattr(layer, "tools", [])]
    contributes = ", ".join(names) if names else "— no tools"
    print(f"  {layer.__class__.__name__:34} {contributes}")
''')

md("""
There it is, and the arithmetic closes exactly:

| layer | what it is for |
|---|---|
| **`FilesystemMiddleware`** | gives the model a computer to work on — 8 tools: `ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `execute` |
| **`SubAgentMiddleware`** | lets it hand work to another agent — 1 tool: `task` |
| **`_DeepAgentsSummarizationMiddleware`** | compresses the conversation when it grows too long to send |
| **`PatchToolCallsMiddleware`** | repairs a history where a tool was called and never answered — the run was killed, or a new message arrived first |
| **`AnthropicPromptCachingMiddleware`** | marks the stable part of the prompt as cacheable |

Eight tools plus one is the nine we counted.

And notice the two kinds of layer. The first two **add capability** — new verbs
the model can use. The last three **change behaviour** — nothing new to call,
but the run survives a long conversation, a killed tool call, a repeated
prefix. A harness is made of both.
""")

md("""
## How a layer injects a tool

"The layer contributed the tools" is still a story. Here is the mechanism, and
it is smaller than you expect.

A middleware is an ordinary Python object. Two things on it matter: **hook
methods**, which notebook 03 is about, and a **`.tools` attribute** — a plain
list.
""")

code("""
from deepagents import FilesystemMiddleware

layer = FilesystemMiddleware()

print(f"FilesystemMiddleware().tools is a {type(layer.tools).__name__} of {len(layer.tools)}:\\n")
for tool in layer.tools:
    summary = (tool.description or "").strip().splitlines()[0]
    print(f"   {tool.name:12} {type(tool).__name__:16} {summary[:46]}")
""")

md("""
Eight `StructuredTool` objects, built when the layer was constructed. Not
registered anywhere, not announced to anything — they are simply *on the
object*.

So what does the assembler do with them? It concatenates.
""")

code("""
from_layers = {
    tool.name
    for layer in captured["middleware"]
    for tool in getattr(layer, "tools", [])
}
on_agent = set(tools)          # what the compiled agent is actually holding

print("union of every layer's .tools :", len(from_layers))
print("tools bound to the agent      :", len(on_agent))
print("the same set                  :", from_layers == on_agent)
""")

md("""
That is the whole injection mechanism: **`create_agent` walks the middleware
list, takes the union of their `.tools`, binds it to the model and builds the
tool node from it.** No registry, no discovery, no plugin protocol. List
concatenation at assembly time.

Two consequences worth carrying forward:

* **Installing a layer is the only way an agent gains a verb.** There is no
  other door. That is why the whole library is layers.
* **The tools are built by the layer, so the layer decides what they do.**
  `FilesystemMiddleware` builds its eight as closures over a *backend* — the
  thing that says where `read_file` reads from. Same eight names, entirely
  different machine underneath.

That second point is the subject of notebook 02.
""")

md("""
## This is what the library is

That was not a quirk of one function call. Look at what the package exports.
""")

code('''
import importlib.metadata as meta

import deepagents

print("deepagents", meta.version("deepagents"))
print()
print("What the library exports:")
for name in sorted(n for n in dir(deepagents) if not n.startswith("_")):
    print("   ", name)
''')

md("""
Read that list again, because it is the whole argument of this notebook.

Almost every name ends in **`Middleware`**. `FilesystemMiddleware`.
`SubAgentMiddleware`. `MemoryMiddleware`. `RubricMiddleware`.

There is exactly one function that builds an agent — `create_deep_agent` — and
it is surrounded by layers. That ratio tells you what the library thinks an
agent *is*.

> **A harness is a middleware stack wrapped around a model.**

Which makes `create_deep_agent` not a constructor but an **assembler**. Its
parameters are almost entirely "which layer, configured how".
""")

code('''
import inspect

params = inspect.signature(create_deep_agent).parameters
print(f"create_deep_agent takes {len(params)} parameters:")
print()
for name in params:
    print("   ", name)
''')

md("""
`tools`, `subagents`, `skills`, `memory`, `permissions`, `backend`,
`interrupt_on`, `checkpointer` — each is a layer being installed, configured or
replaced. You will meet them in roughly that order, because that is roughly the
order in which they matter.
""")

md("""
## What you now know

1. A harness wires **judgement** (the model) to **capability** (the operating
   system). Neither half is new; the wiring is.
2. Because of that, the container an agent runs in *is* the set of things it can
   do. The container is the computer.
3. Mechanically, a harness is a **middleware stack**. `create_deep_agent`
   assembles one: five layers, two of which add tools and three of which change
   how the run behaves.
4. The prompt is a file — `AGENT.md` here, `system_prompt.md` for every role in
   this repo.

What we have not explained is the layer that did all the visible work.
`FilesystemMiddleware` handed the model eight verbs and a machine to point them
at. What exactly is a "machine" here — and what happens when the agent tries to
read something outside it?

That is notebook 02.
""")

nb.cells = C
nbf.write(nb, "notebooks/01_what_a_harness_is.ipynb")
print(f"wrote notebooks/01_what_a_harness_is.ipynb · {len(C)} cells")
