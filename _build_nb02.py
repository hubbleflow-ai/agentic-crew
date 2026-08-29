"""Builds notebooks/02_the_filesystem_middleware.ipynb."""
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
# 02 · The filesystem middleware

Notebook 01 counted nine tools on an agent we gave none to, and printed where
each came from. **Eight of the nine came from one layer:**

`ls` · `read_file` · `write_file` · `edit_file` · `delete` · `glob` · `grep` ·
`execute`

That is not a random selection. It is, almost exactly, what an engineer does at
a terminal.

This is the layer that turns "a model that writes text" into "something that
behaves like an engineer", so it comes first. By the end you will have watched
an agent inspect the machine it is on, write a program, run it, and then fail to
escape the box we put it in.
""")

code("""
import os
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")

ROOT = pathlib.Path.cwd()
while not (ROOT / "pyproject.toml").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
assert os.environ.get("GOOGLE_API_KEY"), "set GOOGLE_API_KEY (see .env)"
print("repo root:", ROOT)
""")

md("""
## First: where the eight tools come from

Notebook 01 printed the stack and attributed the tools to layers. Take that on
trust for exactly one more minute, then watch it happen.

`create_agent` is LangChain's ordinary agent — a model, a prompt, a tool loop,
and whatever tools you hand it. We hand it none.
""")

code("""
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

# A fake model · this cell is about structure, so it costs nothing and never varies.
def fake():
    return FakeMessagesListChatModel(responses=[AIMessage(content="ok")])

def tools_of(agent):
    node = agent.nodes.get("tools")          # an agent with no tools has no node
    return sorted(getattr(node, "bound", node).tools_by_name) if node else []

bare = create_agent(model=fake(), system_prompt="You are an engineer.", tools=[])

print("create_agent, no tools:", len(tools_of(bare)), "tools")
""")

md("""
Zero. As expected — a model emits text, and nobody gave it hands.

So how does a layer put a tool there? Build one. Here is a middleware an
engineer would actually want: it **meters every model call**, and it lets the
agent ask how much of its budget is left.
""")

code("""
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI


class CallBudget(AgentMiddleware):
    \"\"\"Caps how much thinking a task is allowed, and makes the cap visible.\"\"\"

    def __init__(self, max_calls: int = 4) -> None:
        super().__init__()
        self.max_calls = max_calls
        self.used = 0

        @tool
        def budget_left() -> str:
            \"\"\"How many model calls remain before this agent must stop.\"\"\"
            return f"{self.max_calls - self.used} of {self.max_calls} calls left"

        self.tools = [budget_left]        # 1 · what this layer ADDS

    def wrap_model_call(self, request, handler):
        self.used += 1                    # 2 · where this layer STANDS
        print(f"   [budget] model call {self.used}/{self.max_calls}")
        return handler(request)           #     the model runs here


metered = create_agent(
    model=ChatGoogleGenerativeAI(model=MODEL, temperature=0),
    system_prompt="You are an engineer. Use your tools.",
    tools=[],
    middleware=[CallBudget()],
)

print("tools on the agent:", tools_of(metered), "\\n")
result = metered.invoke({"messages": [{"role": "user", "content":
    "How much budget do you have left? Check, then tell me."}]})
answer = result["messages"][-1].content
if isinstance(answer, list):
    answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))
print("\\n", answer)
""")

md("""
**Why the meter ticked twice for one question.**

A model cannot call a tool and keep talking. It stops, emits the call, the graph
runs the tool, and the model must be called *again* with the result to turn it
into an answer:

| | what runs | `used` |
|---|---|---|
| 1 | **model call** — sees the question and `budget_left`'s schema, emits a tool call | 1 |
| 2 | **tool executes** — returns `"3 of 4 calls left"`; not a model call, so no tick | 1 |
| 3 | **model call** — the result is appended and the model is called again to answer | 2 |

That `model -> tools -> model` cycle is the agent loop. So `wrap_model_call`
fires **per model call, not per user turn** — an agent that used four tools in
one turn would tick five times. Which is exactly why a budget is denominated in
model calls: that is the thing that costs money and time.

One detail worth catching: it reports *"3 of 4"* even though `used` was already
2 when it spoke. The tool read the counter at the moment it **asked**, not the
moment it **answered**. A tool result is a snapshot, and by the time the model
is reasoning about it, it is already one step out of date.
""")

md("""
Two things happened, and they are the two things a middleware can be.

**1 · `self.tools` is what it adds.** A plain list. `create_agent` walks the
layers you passed, reads `.tools` off each one, unions them, binds that union to
the model so it can *see* the schemas, and builds the tool node so a call
actually *runs*. No registry, no discovery, no `@register` decorator scanning for
subclasses — list concatenation at assembly time. That is the whole of "tool
injection".

**2 · `wrap_model_call` is where it stands.** It receives the request and a
`handler`, and the model only runs when it calls `handler(request)`. Everything
before that line is "before the model"; everything after is "after". Which means
this hook could also refuse the call, retry it, or return a cached answer
instead — notebook 03 is about that.

And notice *how* `budget_left` was built: **inside `__init__`, closing over the
layer's own state.** That is not a stylistic choice, it is the pattern —
`FilesystemMiddleware` builds its eight tools exactly the same way, closing over
a **backend**. Same eight names, a different machine underneath, depending on
what you passed.
""")

md("""
So `FilesystemMiddleware` is that same trick, eight times over.
""")

code("""
from deepagents import FilesystemMiddleware

plus = create_agent(
    model=fake(),
    system_prompt="You are an engineer.",
    tools=[],
    middleware=[FilesystemMiddleware()],      # <- the only difference
)

print("with FilesystemMiddleware:", len(tools_of(plus)), "tools\\n")
for name in tools_of(plus):
    print("   ", name)
""")

md("""
One line, eight tools. Nobody wrote a tool; nobody described one to the model.

**That is what `FilesystemMiddleware` is: the layer that hands the model a
computer.** And the eight verbs are not a random API surface — they are what a
person does at a terminal:

| tool | the thing you would type |
|---|---|
| `ls` | `ls` |
| `read_file` | `cat` |
| `write_file` | `>` |
| `edit_file` | opening it in an editor |
| `delete` | `rm` |
| `glob` | `find` |
| `grep` | `grep` |
| `execute` | anything else at all |

`execute` is the one to notice. Seven tools are a filesystem; the eighth is the
rest of the operating system.

The ninth tool from notebook 01 — `task`, for handing work to another agent —
comes from `SubAgentMiddleware`, and has notebook 05 to itself.
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

md("""
## The tools are one half. The **backend** is the other.

`FilesystemMiddleware` supplies the verbs. *What those verbs touch* is a second
object, passed as `backend=`.

That sounds like plumbing. Here is the same agent, given the same sentence,
twice — changing nothing but that one argument.
""")

code("""
import inspect
import tempfile

from deepagents import create_deep_agent
from deepagents.backends import (
    CompositeBackend,  # noqa: F401 · named in the table below
    FilesystemBackend,
    LocalShellBackend,  # noqa: F401 · used further down
    StateBackend,
)
from langchain_google_genai import ChatGoogleGenerativeAI

ask = {"messages": [{"role": "user", "content":
    "Write a file notes.md containing the single line: hello from the agent."}]}

def engineer(backend):
    return create_deep_agent(
        model=ChatGoogleGenerativeAI(model=MODEL, temperature=0),
        system_prompt="You write files.",
        backend=backend,
    )

# 1 · a filesystem that exists only inside the conversation
in_state = engineer(StateBackend()).invoke(ask)

# 2 · a filesystem that is a directory on this machine
disk_dir = tempfile.mkdtemp(prefix="nb02-disk-")
on_disk = engineer(FilesystemBackend(root_dir=disk_dir)).invoke(ask)

def on_disk_files(root):
    return [str(f.relative_to(root)) for f in sorted(pathlib.Path(root).rglob("*")) if f.is_file()]

print("StateBackend")
print("   in state:", list((in_state.get("files") or {}).keys()))
print("   on disk :", "-- it has no directory at all")
print()
print("FilesystemBackend")
print("   in state:", list((on_disk.get("files") or {}).keys()) or "nothing")
print("   on disk :", on_disk_files(disk_dir))
""")

md("""
Both agents believed they wrote a file. Both reported success. **Only one left
anything behind.**

`StateBackend` is a filesystem that exists only inside the conversation —
`write_file` puts an entry in the agent's state, and it dies with the process.
`FilesystemBackend` is a directory on a real machine, and the file outlives the
agent completely.

That is what a backend is: **where the verbs land**. It is also why this repo can
have a reviewer read what a backend engineer wrote — a shared volume is only
shareable if the writes were real.

Four ship with the library, and you meet all of them:

| backend | what the agent gets |
|---|---|
| `StateBackend` | a filesystem living in the conversation, gone when it ends |
| `FilesystemBackend` | a real directory, rooted — what our agents use |
| `LocalShellBackend` | a real directory **and a real shell** |
| `CompositeBackend` | different mounts served by different backends |

"The container is the computer", expressed as a constructor argument.
""")

code("""
print("What our agents are actually built with:\\n")
source = (ROOT / "agents/shared/agent_loop.py").read_text()
start = source.index("def _backend")
print(source[start:source.index("def _skill_sources")].rstrip())
""")

md("""
`virtual_mode=True` is doing real work there. Every path the model uses is
rooted at that directory, so a model that asks for `/etc/passwd` gets
`<workspace>/etc/passwd`. It is not a policy the model is asked to respect; it
is arithmetic on the path before the read happens.

And in the cluster the volume is mounted with `subPath: <project-id>`, so one
project's `/workspace` is a different directory on disk from another's. Two
layers, both structural, neither depending on the model's cooperation.
""")

md("""
## It also has a terminal

`execute` is in that list of eight, and it is not a toy. Swap the backend for
`LocalShellBackend` and the agent gets a shell.

Watch it establish what machine it is on, write a program, run it, and report
the output — none of which we walk it through.
""")

code("""
shell_dir = tempfile.mkdtemp(prefix="nb02-shell-")

machine = create_deep_agent(
    model=ChatGoogleGenerativeAI(model=MODEL, temperature=0),
    system_prompt="You are an engineer with a real terminal. Prefer `execute` for shell commands.",
    backend=LocalShellBackend(root_dir=shell_dir),
)

result = await machine.ainvoke({"messages": [{"role": "user", "content":
    "Use execute to find out the kernel (uname -a), the python version and the current user. "
    "Then write fizz.py that prints fizzbuzz to 15, run it with python3, and show me its output."
}]})

for message in result["messages"]:
    for call in getattr(message, "tool_calls", []) or []:
        print(f"   {call['name']:<12} {str(call['args'])[:96]}")

answer = result["messages"][-1].content
if isinstance(answer, list):
    answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))
print()
print(answer)
""")

md("""
Read what just happened carefully, because it is the whole thesis in one cell.

The model did not gain a new ability. It emitted text, exactly as it always
does. A layer turned that text into `execve`, and handed the result back.
**Judgement met capability, and the capability was the operating system's, not
the model's.**

Now read the *commands* it chose, not just the answer. It probed with `pwd`,
listed with `find .`, and on the run that produced this output it decided a
stray directory was untidy and ran **`rm -rf`** on it. Nobody asked it to. It
was not misbehaving — we handed it a shell, and tidying up is what an engineer
does with one.

That command ran on **your machine**, in this kernel, as your user.
`LocalShellBackend` says so in its own docstring: *"unrestricted local shell
command execution"*. Fine for one notebook cell you are watching. Completely
unacceptable for six agents working unattended overnight — which is exactly why
the crew gives each agent a pod instead, and why notebook 05 is about sub-agents
being containers.

> Re-run this cell and the trace will differ — a different model turn makes
> different choices. That variability is the point: you cannot enumerate in
> advance what it will decide to run. You can only choose what it is running
> *on*.
""")

md("""
## The box holds

If the backend is what the agent can touch, then the backend is also the
security boundary. Not the prompt. Not the model's good manners.

Here we hand it a `FilesystemBackend` rooted at an empty directory and ask it
to go looking outside.
""")

code("""
sealed_dir = tempfile.mkdtemp(prefix="nb02-sealed-")

sealed = create_deep_agent(
    model=ChatGoogleGenerativeAI(model=MODEL, temperature=0),
    system_prompt="You are an engineer investigating an unfamiliar machine.",
    backend=FilesystemBackend(root_dir=sealed_dir, virtual_mode=True),
)

result = await sealed.ainvoke({"messages": [{"role": "user", "content":
    "What operating system is this? Read /etc/os-release and /etc/passwd, and list / to find out."
}]})

for message in result["messages"]:
    for call in getattr(message, "tool_calls", []) or []:
        print(f"   {call['name']:<12} {str(call['args'])[:96]}")

answer = result["messages"][-1].content
if isinstance(answer, list):
    answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))
print()
print(answer[:900])
""")

code("""
print("What is actually in the directory we gave it:")
print(sorted(p.name for p in pathlib.Path(sealed_dir).iterdir()) or "   (empty)")
print()
print("And on the real machine, for comparison:")
print("   /etc/passwd exists:", pathlib.Path("/etc/passwd").exists())
""")

md("""
It asked for `/etc/passwd`. `/etc/passwd` exists on this machine. It did not
get it — every path was resolved inside the directory we chose, before the read
happened.

Nothing about that depended on the model being well behaved, on a system prompt
saying "do not read system files", or on a list of forbidden paths that someone
has to keep up to date. **The agent could not reach outside the box because the
box is where paths are resolved.**

Put the other way round: everything the agent *can* do, you granted, by
choosing a backend and a directory.
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

for name, param in inspect.signature(FilesystemMiddleware.__init__).parameters.items():
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

1. Eight of the nine tools come from `FilesystemMiddleware`, and they make an
   agent behave like an engineer without being told to.
2. The **backend** is the other half: `StateBackend` (a filesystem that only
   exists in memory), `FilesystemBackend` (real disk, rooted), `LocalShellBackend`
   (real disk and a real shell), `CompositeBackend` (different mounts, different
   backends). Swapping it changes what the agent is.
3. `virtual_mode` resolves every path inside the root **before** the read, so
   containment does not depend on the model's cooperation. `subPath` does it
   again at the volume.
4. **Automatic eviction** turns large tool output into a file path, so context
   exhaustion stops being the limit on how long an agent can work.
5. The filesystem layer is required, because eviction depends on it.

You have now seen two real layers: one that hands over a computer, one that
hands over a sub-agent. Both were installed for you.

Next: what a layer actually *is*. You will write one in ten lines, and then read
the one this repository runs in production.
""")

nb.cells=C
nbf.write(nb, "notebooks/02_the_filesystem_middleware.ipynb")
print("wrote 02 ·", len(C), "cells")
