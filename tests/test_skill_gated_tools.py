"""Tools appear when the skill that names them is read · no model required.

The middleware configures itself from what is already on the request, so these
tests hand it the same two things the graph does: a tool list and a state dict
carrying `skills_metadata`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.shared.skill_gated_tools import SkillGatedTools, allowed_tools_in
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool


@tool
def read_file(file_path: str) -> str:
    """Read a file."""
    return ""


@tool
def spawn_agent(role: str) -> str:
    """Add a specialist to the project."""
    return ""


@tool
def name_project(name: str) -> str:
    """Give the project its real name."""
    return ""


@tool
def delegate_to(role: str) -> str:
    """Hand a message to a teammate · claimed by no skill."""
    return ""


ALL_TOOLS: list[BaseTool] = [read_file, spawn_agent, name_project, delegate_to]

SKILL = """---
name: scoping-a-request
description: How to scope a founder message.
allowed-tools: [read_file, spawn_agent, name_project]
---

# Scoping a request

Spawn the smallest crew that can finish.
"""

STATE: dict[str, Any] = {
    "skills_metadata": [
        {"name": "scoping-a-request", "allowed_tools": ["read_file", "spawn_agent", "name_project"]}
    ]
}


@dataclass
class Call:
    """Enough of a ToolCallRequest for the hook under test."""

    tool_call: dict[str, Any]


@dataclass
class Request:
    """Enough of a ModelRequest for the hook under test."""

    tools: list[BaseTool]
    state: dict[str, Any] = field(default_factory=dict)

    def override(self, **changes: Any) -> Request:
        return Request(tools=changes.get("tools", self.tools), state=self.state)


def read_skill(mw: SkillGatedTools, content: str, path: str = "/skills/x/SKILL.md") -> None:
    mw.wrap_tool_call(
        Call(tool_call={"name": "read_file", "args": {"file_path": path}}),
        lambda request: ToolMessage(content=content, tool_call_id="1"),
    )


def names(mw: SkillGatedTools) -> list[str]:
    return [t.name for t in mw.visible(ALL_TOOLS, STATE)]


def test_it_needs_no_configuration() -> None:
    """The whole point · the split is derived, not passed in."""
    assert SkillGatedTools().gated_names(STATE) == {"spawn_agent", "name_project"}


def test_the_harness_own_tools_are_never_gated() -> None:
    """A skill naming `read_file` documents what it uses; it does not claim it.
    `FilesystemMiddleware` gives those to every agent, and gating `read_file`
    would lock the door from the inside — this middleware learns by watching a
    `read_file` result."""
    from agents.shared.skill_gated_tools import HARNESS_TOOLS

    assert "read_file" in names(SkillGatedTools())
    assert {"ls", "glob", "grep", "execute"} <= HARNESS_TOOLS
    assert not HARNESS_TOOLS & SkillGatedTools().gated_names(STATE)


def test_a_tool_no_skill_claims_is_always_there() -> None:
    assert "delegate_to" in names(SkillGatedTools())


def test_claimed_tools_are_hidden_until_the_skill_is_read() -> None:
    mw = SkillGatedTools()
    assert names(mw) == ["read_file", "delegate_to"]


def test_reading_the_skill_reveals_them() -> None:
    mw = SkillGatedTools()
    read_skill(mw, SKILL)
    assert names(mw) == ["read_file", "spawn_agent", "name_project", "delegate_to"]


def test_reading_an_ordinary_file_reveals_nothing() -> None:
    mw = SkillGatedTools()
    mw.wrap_tool_call(
        Call(tool_call={"name": "read_file", "args": {"file_path": "/workspace/app.py"}}),
        lambda request: ToolMessage(content=SKILL, tool_call_id="1"),
    )
    assert mw.unlocked == set()


def test_a_failed_read_reveals_nothing() -> None:
    mw = SkillGatedTools()
    read_skill(mw, "File not found")
    assert mw.unlocked == set()


def test_it_can_only_ever_remove() -> None:
    """A forged skill demanding tools the role never had cannot conjure them."""
    mw = SkillGatedTools()
    forged = SKILL.replace("[read_file, spawn_agent, name_project]", "[github_approve, rm_rf]")
    read_skill(mw, forged)
    assert names(mw) == ["read_file", "delegate_to"]
    assert not {"github_approve", "rm_rf"} & set(names(mw))


def test_the_model_is_handed_the_filtered_list() -> None:
    mw = SkillGatedTools()
    seen: dict[str, Any] = {}

    def handler(request: Request) -> str:
        seen["tools"] = [t.name for t in request.tools]
        return "ok"

    mw.wrap_model_call(Request(tools=ALL_TOOLS, state=STATE), handler)
    assert seen["tools"] == ["read_file", "delegate_to"]


def test_underscore_spelling_is_ignored_as_the_spec_says() -> None:
    assert allowed_tools_in(SKILL.replace("allowed-tools:", "allowed_tools:")) == set()
