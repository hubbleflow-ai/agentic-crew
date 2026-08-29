"""Tools that arrive when the skill naming them is read.

`SkillsMiddleware` discloses *instructions* progressively: the system prompt
carries each skill's name and description, and the body is fetched with
`read_file` only when the model decides it applies. It does not do the same for
*tools*. Every tool a role has is bound at assembly, and its full JSON schema is
resent on every model call — for the Engineering Manager that is ~1,500 tokens
of schema before a word of conversation.

The library gets within one line of closing that gap. It parses each skill's
`allowed-tools` frontmatter and prints it into the prompt as advice. Nothing
enforces it: `SkillsMiddleware.wrap_model_call` overrides only `system_message`,
never `tools`.

This middleware connects them, and it takes **no configuration to do it**.
Everything it needs is already on the request:

* ``request.tools`` — every tool the agent was built with.
* ``request.state["skills_metadata"]`` — what each skill declares, put there by
  `SkillsMiddleware` at ``before_agent``.

So the split is *derived from the skills themselves*: *a tool is gated if some
skill claims it*. Nothing to keep in sync, and a new skill changes the gate by
existing.

**It only ever removes.** `visible()` filters `request.tools`; it never appends.
A skill is a file — it can be wrong, edited, or written by another agent — so a
skill naming `github_approve` cannot conjure one for a role that was never given
it. The worst a bad skill can do is unlock something the agent already had.

**The harness's own tools are never gated**, and the list is not guessed — it is
asked for. `FilesystemMiddleware` supplies `ls`, `read_file`, `write_file`,
`edit_file`, `delete`, `glob`, `grep` and `execute` to *every* agent; a skill
naming one of them in `allowed-tools` is documenting what it uses, not claiming
ownership of it. Withholding them would also lock the door from the inside:
this middleware learns by watching a `read_file` result, so hiding `read_file`
means the agent can never open the skill that would return it.

What is left to gate is exactly what the **role** was given — `spawn_agent`,
`write_ticket`, `github_approve` — which is the interesting half anyway, and the
expensive one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import yaml
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from agents.shared.logging_setup import setup_logging

log = setup_logging("skill-gated-tools")

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

SKILL_FILE = "SKILL.md"
"""Reading one of these is what unlocks tools."""

TRIGGER_TOOL = "read_file"
"""How a skill is read · so it is never gated, whatever a skill declares."""


def _harness_tools() -> frozenset[str]:
    """The tools `FilesystemMiddleware` gives every agent.

    Asked of the library rather than written down here, so a version that adds
    a ninth verb does not silently start gating it.
    """
    from deepagents import FilesystemMiddleware

    return frozenset({tool.name for tool in FilesystemMiddleware().tools}) | {TRIGGER_TOOL}


HARNESS_TOOLS = _harness_tools()
"""Supplied by the harness to everyone · never a role's to withhold."""


def allowed_tools_in(content: str) -> set[str]:
    """The `allowed-tools` a SKILL.md declares, or nothing.

    Note the hyphen. The Agent Skills spec — and `deepagents` — read
    ``allowed-tools``; ``allowed_tools`` is ignored in silence, which is a
    whole afternoon if you meet it cold.
    """
    match = _FRONTMATTER.match(content)
    if not match:
        return set()
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return set()
    if not isinstance(data, dict):
        return set()
    declared = data.get("allowed-tools") or []
    if isinstance(declared, str):
        declared = [part.strip() for part in declared.split(",")]
    return {str(name).strip() for name in declared if str(name).strip()}


class SkillGatedTools(AgentMiddleware):
    """Hide a tool until a skill that claims it has been read."""

    def __init__(self) -> None:
        super().__init__()
        self.unlocked: set[str] = set()

    # ─── the trigger ─────────────────────────────────────────────────────

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        """Watch reads of a SKILL.md and unlock whatever it declares.

        Deliberately *after* the handler: a read that failed unlocks nothing,
        so a hallucinated skill path cannot widen the agent's reach.
        """
        result = handler(request)

        call = getattr(request, "tool_call", {}) or {}
        if call.get("name") != TRIGGER_TOOL:
            return result
        path = str((call.get("args") or {}).get("file_path", ""))
        if not path.endswith(SKILL_FILE):
            return result

        declared = allowed_tools_in(str(getattr(result, "content", result) or ""))
        if declared - self.unlocked:
            self.unlocked |= declared
            log.info("tools.unlocked skill=%s tools=%s", path, sorted(declared))
        return result

    # ─── what the model is shown ─────────────────────────────────────────

    def gated_names(self, state: Any) -> set[str]:
        """Every tool some skill claims, except the ones the harness supplies."""
        claimed: set[str] = set()
        for skill in (state or {}).get("skills_metadata", []) or []:
            fields = skill if isinstance(skill, dict) else vars(skill)
            claimed |= {str(name) for name in (fields.get("allowed_tools") or [])}
        return claimed - HARNESS_TOOLS

    def visible(self, tools: Any, state: Any) -> list[Any]:
        """`tools`, minus anything gated that no skill has unlocked yet."""
        gated = self.gated_names(state)
        return [
            tool
            for tool in (tools or [])
            if getattr(tool, "name", None) not in gated - self.unlocked
        ]

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(request.override(tools=self.visible(request.tools, request.state)))
