"""Builds notebooks/01_what_a_harness_is.ipynb."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)",
                              "language": "python", "name": "python3"}}
C = []
def md(t): C.append(nbf.v4.new_markdown_cell(t.strip()))
def code(t): C.append(nbf.v4.new_code_cell(t.strip()))

md("""
# 01 · What a harness is

Claude Code. Codex. Pi. Hermes. DeepAgents.

These are not five products that happen to resemble each other. They are five
instances of one thing, and that thing has a name: a **harness**.

By the end of this notebook you will be able to say what a harness is in one
sentence, and you will have seen the mechanism that makes one — printed, but
not yet explained.
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
## What we actually run here

Everything below runs against the code in this repository. Two things need to
be true first.

**A Python environment with the project's dependencies.** From the repo root:

```bash
uv venv && uv pip install -r requirements.txt
```

**A cluster, for the notebooks from 05 onward.** Notebooks 01–04 need nothing
but the library.

```bash
minikube start
./scripts/deploy.sh
```
""")

code("""
import deepagents, importlib.metadata as meta

print("deepagents", meta.version("deepagents"))
print()
print("What the library exports:")
for name in sorted(n for n in dir(deepagents) if not n.startswith("_")):
    print("   ", name)
""")

md("""
Read that list again, because it is the whole argument of this notebook.

Almost every name ends in **`Middleware`**. `FilesystemMiddleware`.
`SubAgentMiddleware`. `MemoryMiddleware`. `RubricMiddleware`.

There is exactly one function that builds an agent — `create_deep_agent` — and
it is surrounded by middleware classes. That ratio is telling you what the
library thinks an agent *is*.
""")

md("""
## The mechanism, named but not explained

Here is a harness, built the simplest way the library allows: a model, a
prompt, and nothing else. No tools. No configuration.

Then we ask what it is made of.
""")

code("""
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

# A stand-in model · this cell is about structure, not about answers,
# and a fake model means it costs nothing and never varies.
model = FakeMessagesListChatModel(responses=[AIMessage(content="ok")])

agent = create_deep_agent(model=model, system_prompt="You are a helpful engineer.")

# We passed no tools at all. Ask the compiled graph what it has anyway.
tool_node = agent.nodes["tools"]
tools = sorted(getattr(tool_node, "bound", tool_node).tools_by_name)

print(f"We asked for no tools. The agent has {len(tools)}:")
print()
for name in tools:
    print("   ", name)
""")

md("""
We passed a model and a sentence. We got back a thing that can list a
directory, read a file, edit a file, glob, grep, delete, run a command
(`execute`), and hand work to a sub-agent (`task`).

That is not a small default. That is most of what a junior engineer does at a
terminal, and none of it was requested.

Where did it come from? Ask the graph what it is made of.
""")

code("""
print("Nodes in the compiled graph:")
for node in agent.get_graph().nodes:
    print("   ", node)
""")

md("""
`PatchToolCallsMiddleware.before_agent`. A node named after a **middleware**
and the **hook** it implements.

That is the mechanism. Those nine tools were not built into some Agent class —
each arrived because a middleware was installed and contributed them.

> **A harness is a middleware stack wrapped around a model.**

`create_deep_agent` is not really a constructor. It is an **assembler**: its
eighteen parameters are almost entirely about which layers to install, and how
to configure them.
""")

code("""
import inspect

params = inspect.signature(create_deep_agent).parameters
print(f"create_deep_agent takes {len(params)} parameters:")
print()
for name in params:
    print("   ", name)
""")

md("""
`tools`, `subagents`, `skills`, `memory`, `permissions`, `backend`,
`interrupt_on`, `checkpointer` — each one of those is a middleware being
configured, or installed, or replaced.

You will meet them in that order over the next few notebooks, because that is
roughly the order in which they matter.
""")

md("""
## What you now know

1. A harness wires **judgement** (the model) to **capability** (the operating
   system). Neither half is new; the wiring is.
2. Because of that, the container an agent runs in *is* the set of things it
   can do. The container is the computer.
3. Mechanically, a harness is a **middleware stack**. `create_deep_agent`
   assembles one for you.

What we have not done is explain what a middleware actually *is* — what it can
see, when it runs, what it can change.

That is notebook 02, and it is a smaller idea than the name suggests. You will
write one in about ten lines.
""")

nb.cells = C
nbf.write(nb, "notebooks/01_what_a_harness_is.ipynb")
print("wrote notebooks/01_what_a_harness_is.ipynb ·", len(C), "cells")
