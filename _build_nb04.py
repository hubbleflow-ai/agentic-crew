"""Builds notebooks/04_skills_and_progressive_disclosure.ipynb."""
import nbformat as nbf
nb = nbf.v4.new_notebook()
nb.metadata = {"kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"}}
C=[]
def md(t): C.append(nbf.v4.new_markdown_cell(t.strip()))
def code(t): C.append(nbf.v4.new_code_cell(t.strip()))

md("""
# 04 · Skills, and paying only for what you read

Notebook 03 made the case that a filesystem is where an agent keeps its work.

This one makes a stranger case: it is also where an agent keeps its
**instructions**.

The reason is arithmetic. A system prompt is sent on every model call. If you
want an agent to know your ticket format, your review standards, your Python
conventions and your escalation policy, and you put all of that in the prompt,
you pay for all of it on every turn — including the turns where none of it is
relevant.
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
import os
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage

assert os.environ.get("GOOGLE_API_KEY"), "set GOOGLE_API_KEY (see .env)"

agent = create_deep_agent(
    model=ChatGoogleGenerativeAI(model="gemini-3.5-flash"),
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
## What you now know

1. A skill is a directory with `SKILL.md`: frontmatter the model always sees,
   a body it reads on demand.
2. The standing cost is the description line. That is what makes it affordable
   to give an agent a lot of specific knowledge.
3. Which skills a role can see is decided by which directories are in its
   source list — scoping by construction, not by instruction.
4. The description is a *when*, not a *what*.

Next: how one agent becomes several, and why in this repo a sub-agent is a pod.
""")

nb.cells=C
nbf.write(nb, "notebooks/04_skills_and_progressive_disclosure.ipynb")
print("wrote 04 ·", len(C), "cells")
