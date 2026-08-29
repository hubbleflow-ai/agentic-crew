"""Builds notebooks/04_skills_and_progressive_disclosure.ipynb."""
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
# 04 · Skills, and paying only for what you read

Notebook 02 made the case that a filesystem is where an agent keeps its work.

This one makes a stranger case: it is also where an agent keeps its
**instructions**.

The reason is arithmetic. A system prompt is sent on every model call. If you
want an agent to know your ticket format, your review standards, your Python
conventions and your escalation policy, and you put all of that in the prompt,
you pay for all of it on every turn — including the turns where none of it is
relevant.
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
print("repo root:", ROOT)
""")

md("""
## What a skill is

A directory with a `SKILL.md` in it. YAML frontmatter, then a markdown body.
""")

code("""
skill = ROOT / "skills/roles/engineering-manager/scoping-a-request/SKILL.md"
text = skill.read_text()

print("=" * 66)
print(text[:text.index("---", 4) + 3])          # the frontmatter
print("=" * 66)
print(f"...then {len(text.splitlines())} lines of body, which is the part")
print("the model does NOT see until it asks.")
""")

md("""
## Progressive disclosure

At `before_agent`, `SkillsMiddleware` reads **only the frontmatter** of every
skill it can see, and injects a short index into the system prompt: name,
description, path.

That is all the model gets. If it decides a skill applies, it calls
`read_file` on the path and gets the body.

So the standing cost of a skill is its description line. The body is paid for
only on the turns where it is used.
""")

code("""
total = 0
print(f"{'skill':<32} {'description':<10} {'body'}")
print("-" * 62)
for path in sorted(ROOT.glob("skills/**/SKILL.md")):
    text = path.read_text()
    end = text.index("---", 4) + 3
    head, body = text[:end], text[end:]
    total += len(body)
    print(f"{path.parent.name:<32} {len(head):>6} ch {len(body):>7} ch")
print("-" * 62)
print(f"{'':<32} {'':>6}    {total:>7} ch of body, loaded only on demand")
""")

md("""
## Which skills a role can see

The layering is in one function, and what it *omits* is the point.
""")

code("""
src = (ROOT / "agents/shared/agent_loop.py").read_text()
start = src.index("def _skill_sources")
print(src[start:src.index("def _as_prompt")].rstrip())
""")

code("""
import agents.shared.agent_loop as loop

# The resolver checks the image path; point it at the checkout instead.
loop.SKILLS_ROOT = str(ROOT / "skills")

for role in ("engineering_manager", "backend_engineer", "qa_engineer"):
    print(f"{role:<22} {loop._skill_sources(role)}")
""")

md("""
A backend engineer's list contains `skills/base` and its own directory. It does
not contain `skills/roles/engineering-manager`.

So it cannot read the scoping playbook — not because it is told not to, but
because the path was never in its list. That is worth contrasting with the
usual approach of writing "do not attempt to scope projects" into a prompt and
hoping.
""")

md("""
## Does the model actually do this?

Give the EM six skills and one ambiguous request, then count what it opened.
""")

code("""
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage

assert os.environ.get("GOOGLE_API_KEY"), "set GOOGLE_API_KEY (see .env)"

agent = create_deep_agent(
    model=ChatGoogleGenerativeAI(model=MODEL, temperature=0),
    system_prompt="You are an Engineering Manager. Consult your skills.",
    backend=FilesystemBackend(root_dir=str(ROOT), virtual_mode=False),
    skills=[str(ROOT / "skills/base"), str(ROOT / "skills/roles/engineering-manager")],
)

result = await agent.ainvoke({"messages": [{"role": "user", "content":
    "A founder says: 'Make onboarding better'. What is your first move?"}]})

opened = [tc["args"].get("file_path", "") for m in result["messages"]
          if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
          if tc["name"] == "read_file"]

print(f"Skills offered:  {len(list(ROOT.glob('skills/base/*/SKILL.md')) + list(ROOT.glob('skills/roles/engineering-manager/*/SKILL.md')))}")
print(f"Skills opened:   {len(opened)}")
for p in opened:
    print("   ", p.replace(str(ROOT), "."))
""")

md("""
It opened the one that matched. The others cost their description line and
nothing else.

## Writing a good description

The description is the only thing the model sees before it chooses, so it
should answer *when would I need this*, not *what is in here*.

> Good: "How to turn a vague founder message into a named project and a ticket"
> Bad: "Project scoping documentation"

The second one is accurate and useless. The model cannot tell from it whether
the request in front of it qualifies.
""")

md("""
## First, a field that does less than it looks like

`SKILL.md` supports one more piece of frontmatter:

```yaml
allowed-tools: [read_ticket, write_ticket, name_project, spawn_agent, escalate_to_founder]
```

**The spelling is `allowed-tools`, with a hyphen.** `deepagents` reads exactly
that key. Write `allowed_tools` and the value is dropped without a warning — the
skill loads, the field is silently empty, and nothing tells you. Every skill in
this repo had it wrong until it was measured.
""")

md("""
## Finishing the idea: tools that arrive with the skill

Everything above disclosed *instructions* progressively: the prompt carries
each skill's name and description, and the body is fetched with `read_file`
only if the model decides it applies.

Tools are not treated that way. Every tool a role has is bound at assembly and
its full JSON schema is resent on **every** model call. For the Engineering
Manager that is roughly 1,500 tokens of schema before a word of conversation,
against ~440 tokens for the entire skills table of contents. The thing that is
*not* disclosed lazily costs about three times the thing that is.

The library gets within one line of closing the gap.
""")

code("""
import inspect

from deepagents.middleware.skills import SkillsMiddleware

# Every override the skills layer performs, in its whole 1,000-line module:
source = inspect.getsource(SkillsMiddleware)
for line in source.splitlines():
    if ".override(" in line:
        print(" ", line.strip())
""")

md("""
`system_message` and nothing else. `SkillsMiddleware` parses each skill's
`allowed-tools` frontmatter and prints it into the prompt as *advice* — it never
touches `request.tools`.

So it has every ingredient: the field is parsed, the hook exists, `override()`
is already being called. It simply does not connect them.

`agents/shared/skill_gated_tools.py` connects them — and takes **no
configuration to do it**. Both halves are already on the request: `request.tools`
is everything the agent was built with, and `request.state["skills_metadata"]` is
what `SkillsMiddleware` recorded about each skill at `before_agent`.

So the rule is derived from the skills themselves: **a tool is gated if some
skill claims it.** Nothing to keep in sync, and a new skill changes the gate
simply by existing. Reading the skill is the trigger — the very `read_file` call
you watched the EM make above — so there is no extra round trip and no second
model guessing what is relevant.
""")

code("""
from agents.shared.skill_gated_tools import HARNESS_TOOLS, SkillGatedTools

gate = SkillGatedTools()          # <- no arguments. It reads the request.

# The two things it needs are already there: every tool the agent was built
# with, and what SkillsMiddleware recorded about each skill at before_agent.
tools = ["read_file", "spawn_agent", "name_project", "delegate_to"]
state = {"skills_metadata": [
    {"name": "scoping-a-request",
     "allowed_tools": ["read_file", "write_ticket", "name_project", "spawn_agent"]},
]}


class T:                                   # stand-in for a bound tool
    def __init__(self, name): self.name = name

    def __repr__(self): return self.name


bound = [T(n) for n in tools]

print("a skill claims          :", sorted(gate.gated_names(state) | {"read_file"}))
print("of those, gated         :", sorted(gate.gated_names(state)))
print("never gated             :", sorted(HARNESS_TOOLS), "· the harness supplies these")
print()
print("before any skill is read:", gate.visible(bound, state))
""")

md("""
Two things to notice.

`read_file` is claimed by the skill and is **not** gated, and neither is any
other tool the *harness* supplies. A skill listing `ls` in `allowed-tools` is
documenting what it uses — it does not own it. `FilesystemMiddleware` gives
those eight verbs to every agent, so they were never a skill's to withhold.

That list is **asked for, not written down**: `{t.name for t in
FilesystemMiddleware().tools}`. A library version that adds a ninth verb does
not silently start gating it.

There is a second reason `read_file` in particular can never be gated. This
middleware learns by watching a `read_file` *result* — hide it and the agent can
never open the skill that would return it. The door, locked from the inside.

What remains gated is exactly what the **role** was given — `spawn_agent`,
`write_ticket`, `name_project` — which is the interesting half, and the
expensive one.

`delegate_to` is not gated either, for the opposite reason — **no skill claims
it**, so it was never the skills' to withhold.

Now the agent gets a real request, decides `scoping-a-request` applies, and reads
it — the same `read_file` call you watched the EM make above.
""")

code("""
from langchain_core.messages import ToolMessage

skill_path = "/skills/roles/engineering-manager/scoping-a-request/SKILL.md"
skill_text = (ROOT / "skills/roles/engineering-manager/scoping-a-request/SKILL.md").read_text()


class Call:
    def __init__(self, name, args): self.tool_call = {"name": name, "args": args}


gate.wrap_tool_call(
    Call("read_file", {"file_path": skill_path}),
    lambda request: ToolMessage(content=skill_text, tool_call_id="1"),
)

print("after reading it        :", gate.visible(bound, state))
""")

md("""
The tools arrived with the instructions that explain them, and the agent that
never needed them never paid for them.

One property matters more than the saving. **It can only ever remove.**
`visible()` filters the list it was handed; it never appends. A skill is a file —
it can be wrong, edited, or written by another agent — so a skill naming a tool
the role was never given must not conjure one.
""")

code("""
forged = skill_text.replace(
    "allowed-tools: [read_ticket, write_ticket, name_project, spawn_agent, escalate_to_founder]",
    "allowed-tools: [github_approve, rm_rf]",
)
gate.wrap_tool_call(
    Call("read_file", {"file_path": "/skills/forged/SKILL.md"}),
    lambda request: ToolMessage(content=forged, tool_call_id="2"),
)
print("after a skill demanding tools this role never had:")
print("  ", gate.visible(bound, state))
""")

md("""
Unchanged. `github_approve` was never in `request.tools`, so no amount of asking
puts it there — the middleware can only ever hand back a **subset** of what the
role already had. Same argument as `virtual_mode` in notebook 02: containment by
construction, not by asking the model nicely.

### The library's own answers

Two ship with LangChain, and both are worth knowing before writing your own:

| | how it defers | works with Gemini |
|---|---|---|
| `LLMToolSelectorMiddleware` | a second, smaller model picks the *n* most relevant tools per turn | **yes** — client-side |
| `ProviderToolSearchMiddleware` | the provider indexes the tools server-side | **no** — `anthropic` and `openai` only |

`ProviderToolSearchMiddleware` would be the clean answer and this crew runs
Gemini, which rules it out. `LLMToolSelectorMiddleware` costs an extra model
call every turn and can hide a tool the agent needed. Ours costs neither, at the
price of a rule the skills have to keep: name the tools you use.

Notebook 06 returns to this as an example of its theme — a layer whose whole job
is to hand the model *less*.
""")

md("""
## What you now know

1. A skill is a directory with `SKILL.md`: frontmatter the model always sees,
   a body it reads on demand.
2. The standing cost is the description line. That is what makes it affordable
   to give an agent a lot of specific knowledge.
3. Which skills a role can see is decided by which directories are in its
   source list — scoping by construction, not by instruction.
4. The description is a *when*, not a *what*.
5. `allowed-tools` is a hint, not a gate — and the underscore spelling is
   ignored in silence. `SkillGatedTools` turns it into one, so the tools arrive
   with the instructions that explain them.

Next: how one agent becomes several, and why in this repo a sub-agent is a pod.
""")

nb.cells=C
nbf.write(nb, "notebooks/04_skills_and_progressive_disclosure.ipynb")
print("wrote 04 ·", len(C), "cells")
