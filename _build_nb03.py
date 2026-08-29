"""Builds notebooks/03_a_middleware_by_hand.ipynb."""
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
# 03 · A middleware, built by hand

You have now met two real layers without ever being told what a layer *is*.
`FilesystemMiddleware` handed the agent a computer. `SubAgentMiddleware` handed
it a colleague. Both arrived installed.

This notebook opens one up. You will see where a layer plugs into the run, write
one in about ten lines, watch it fire, and then read the one this repository
actually runs in production — which is the same shape, only longer.

Middleware is a smaller idea than the word suggests. It is a set of **hooks**.
""")

md("""
## The six places you can stand

An agent turn is a loop: assemble a request, call the model, run whatever tools
it asked for, call the model again with the results, until it stops asking.

A middleware is code that runs at named points in that loop.
""")

code("""
import os
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")

# Notebooks run from notebooks/ · every path below is relative to the repo
# root, so find it once and work from there.
ROOT = pathlib.Path.cwd()
while not (ROOT / "pyproject.toml").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
print("repo root:", ROOT)
""")

code("""
import inspect
from langchain.agents.middleware import AgentMiddleware

hooks = {n for n, _ in inspect.getmembers(AgentMiddleware, inspect.isfunction)
         if not n.startswith("__")}

# Each hook has an async twin named with a leading 'a'. Drop a name only if
# stripping that 'a' leaves another hook -- otherwise `after_model` gets
# mistaken for the async twin of `fter_model`.
sync = sorted(h for h in hooks if not (h.startswith("a") and h[1:] in hooks))
print("Hooks (sync form; each has an async twin prefixed with 'a'):")
print()
for h in sync:
    print("   ", h)
""")

md("""
Read them as a timeline:

| Hook | Fires | Sees |
|---|---|---|
| `before_agent` | once, at the start | the whole state, before anything happens |
| `before_model` | every model call | the state about to be turned into a request |
| `wrap_model_call` | around every model call | the request *and* the response |
| `after_model` | every model response | what the model just said |
| `wrap_tool_call` | around every tool call | the call **and** its result |
| `after_agent` | once, at the end | the final state |

The two `wrap_*` hooks are the interesting ones. `before`/`after` hooks
*observe*. A `wrap_*` hook sits **around** the thing — it receives a `handler`
and decides whether, when, and with what to call it.

That means a `wrap_*` hook can rewrite the input, retry on failure, substitute
a cached result, refuse outright, or time the call. All the interesting
behaviour in a harness lives in wrapping.
""")

md("""
## They are not an abstraction — they are nodes

A hook is not a convention or a callback registry. When the agent is compiled,
the hooks that need to be a *step in the run* become **nodes in the graph**,
named after the class and the hook.

Here is a middleware that implements all six, attached to an agent, compiled.
""")

code("""
from langchain.agents.middleware import AgentMiddleware


class Loud(AgentMiddleware):
    \"\"\"Implements every hook, does nothing in any of them.\"\"\"

    def before_agent(self, state, runtime): return None
    def before_model(self, state, runtime): return None
    def after_model(self, state, runtime): return None
    def after_agent(self, state, runtime): return None
    def wrap_model_call(self, request, handler): return handler(request)
    def wrap_tool_call(self, request, handler): return handler(request)


from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

fake = FakeMessagesListChatModel(responses=[AIMessage(content="ok")])
compiled = create_deep_agent(model=fake, system_prompt="x", middleware=[Loud()])

print("Nodes in the compiled graph:")
for node in compiled.get_graph().nodes:
    print("   ", node)
""")

md("""
Four of the six became nodes. `wrap_model_call` and `wrap_tool_call` did not —
they wrap the `model` and `tools` nodes that are already there, which is exactly
what "around the call" means.

Now look at the default stack, with no middleware of ours at all.
""")

code("""
plain = create_deep_agent(model=fake, system_prompt="x")

print("Nodes in a stock deep agent:")
for node in plain.get_graph().nodes:
    print("   ", node)
""")

md("""
One middleware node — `PatchToolCallsMiddleware.before_agent` — and now it reads
as a sentence rather than a barcode: *the layer that repairs unanswered tool
calls, running once before the agent starts.*

Which also explains something notebook 01 left hanging. Five layers were
installed there, and only one shows up here. The other four contribute **tools**
and **wrappers**, and neither of those is a step in the graph.

> The node list is not the stack. It is the part of the stack that had to become
> a step.
""")

md("""
## Ten lines

Here is a middleware that does one thing: announce every tool call and what it
returned.

Note what it does **not** need. It is not told which tools exist. It does not
register anything. It does not know what agent it is attached to.
""")

code("""
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware


class Narrator(AgentMiddleware):
    \"\"\"Says out loud what the agent is doing.\"\"\"

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        name = request.tool_call["name"]
        args = request.tool_call["args"]
        print(f"  -> calling {name}({args})")

        result = await handler(request)          # the tool actually runs here

        preview = str(getattr(result, "content", result))[:60]
        print(f"  <- {name} returned {preview}")
        return result
""")

md("""
The `handler(request)` line is where the tool runs. Everything before it is
"before the call", everything after is "after the call", and because both live
in one function you never have to match a result back to the call that produced
it.

Hold on to that. It is the entire reason this repo's telemetry is simple.
""")

md("""
## Watch it fire

A real model, two tools, one question that needs both.
""")

code("""
from langchain_google_genai import ChatGoogleGenerativeAI

assert os.environ.get("GOOGLE_API_KEY"), "set GOOGLE_API_KEY (see .env)"


async def celsius_to_fahrenheit(celsius: float) -> float:
    \"\"\"Convert a temperature in Celsius to Fahrenheit.\"\"\"
    return celsius * 9 / 5 + 32


async def describe(fahrenheit: float) -> str:
    \"\"\"Describe a Fahrenheit temperature in plain words.\"\"\"
    if fahrenheit < 32:
        return "freezing"
    if fahrenheit < 60:
        return "cold"
    if fahrenheit < 85:
        return "pleasant"
    return "hot"


agent = create_deep_agent(
    model=ChatGoogleGenerativeAI(model=MODEL, temperature=0),
    system_prompt="Use the tools. Convert, then describe. Answer in one sentence.",
    tools=[celsius_to_fahrenheit, describe],
    middleware=[Narrator()],
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": "Is 31 degrees Celsius pleasant?"}]}
)
answer = result["messages"][-1].content
if isinstance(answer, list):  # Gemini returns content blocks
    answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))
print()
print("Final answer:", answer)
""")

md("""
Every tool call the model made, narrated, in order — without the agent knowing
it was being watched.

That is the property worth naming. **The middleware did not change what the
agent does.** Same model, same tools, same answer. It changed what is *visible*
about it.

Most of a harness is exactly this: layers that add capability or visibility
without the layers below them knowing.
""")

md("""
## The same shape, in this repository

Now read the real one. Every crew agent runs it, and it is why you can watch a
pod think.
""")

code("""
import pathlib

source = (ROOT / "agents/shared/telemetry.py").read_text()

# The two hooks, without the helper functions underneath.
start = source.index("    async def awrap_tool_call")
end = source.index("    async def _emit")
print(source[start:end])
""")

md("""
Compare it with `Narrator` above and the differences are small and practical:

* it publishes an `Event` onto the project's channel instead of printing
* it times the call, so a slow tool is visible as a slow tool
* it reports a tool that **raised**, rather than letting the failure vanish
* `_emit` swallows publishing errors — an agent should not die because its
  event bus hiccuped

The shape is identical. `wrap_tool_call` around the call, one frame, no
correlation.

This replaced about sixty lines that streamed graph updates and reassembled
them by hand: matching tool calls to results by position, de-duplicating on
message ids, and re-reading the whole thing whenever the graph changed shape.
""")

md("""
## Installing one

Two ways, and the difference matters.
""")

code("""
from langchain.agents.middleware import ModelCallLimitMiddleware, SummarizationMiddleware

print("1 · Pass it directly — for middleware you wrote:\\n")
print("      create_deep_agent(model=..., middleware=[Narrator()])\\n")

print("2 · Pass a parameter — for the ones the library ships.")
print("    These are middleware *configuration*, not something separate:\\n")
for param, mw in [
    ("tools=[...]", "the tool node"),
    ("skills=[...]", "SkillsMiddleware"),
    ("subagents=[...]", "SubAgentMiddleware"),
    ("backend=...", "FilesystemMiddleware"),
    ("interrupt_on={...}", "HumanInTheLoopMiddleware"),
    ("permissions={...}", "tool exclusion + interrupts"),
]:
    print(f"      {param:<22} -> {mw}")

print()
print("Available off the shelf, among others:")
for cls in (SummarizationMiddleware, ModelCallLimitMiddleware):
    first_line = (cls.__doc__ or "").strip().split(chr(10))[0]
    print(f"      {cls.__name__:<28} {first_line[:60]}")
""")

md("""
## What you now know

1. A middleware is a set of **hooks** into the agent loop.
2. `before_*`/`after_*` observe; `wrap_*` sits **around** the call and can
   change, retry, refuse or time it. Wrapping is where the power is.
3. Because a `wrap_tool_call` sees the call and its result in one frame,
   nothing downstream has to correlate them.
4. `create_deep_agent`'s parameters are mostly middleware configuration.

5. The hooks that must be a step in the run appear as **nodes**, named
   `Class.hook`. The ones that wrap do not.

You can now read any layer in the library, because they are all this shape.
Next: `SkillsMiddleware` — instructions are files too, and the crew's knowledge
is deliberately not in its system prompt.
""")

nb.cells = C
nbf.write(nb, "notebooks/03_a_middleware_by_hand.ipynb")
print("wrote notebooks/03_a_middleware_by_hand.ipynb ·", len(C), "cells")
