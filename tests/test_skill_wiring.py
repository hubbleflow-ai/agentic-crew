"""The skills an agent boots with · the failure here is silent, so it is tested.

`CompositeBackend` re-prefixes the paths `ls` returns using
``route_prefix[:-1]``, so a route registered as ``"/skills"`` rather than
``"/skills/"`` hands back paths one character short. Every subsequent read
misses, `SkillsMiddleware` finds nothing, and each agent boots unskilled —
with no error, no warning, and an agent that answers anyway, slightly worse.

That is the whole reason this file exists: nothing else would notice.
"""

from __future__ import annotations

import pathlib
import tempfile

import agents.shared.agent_loop as agent_loop
import pytest
from apps.control_plane.domain.caps import AgentRole
from deepagents.backends import CompositeBackend
from deepagents.middleware.skills import _list_skills_with_errors

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> CompositeBackend:
    """The real `_backend()`, with the image's /app/skills pointed at the repo."""
    monkeypatch.setattr(agent_loop, "SKILLS_ROOT", str(REPO / "skills"))
    monkeypatch.setattr(agent_loop, "WORKSPACE", tempfile.mkdtemp(prefix="skills-test-"))
    return agent_loop._backend()


def _names(backend: CompositeBackend, source: str) -> list[str]:
    found, error = _list_skills_with_errors(backend, source)
    assert error is None, f"{source}: {error}"
    return [s.get("name") if isinstance(s, dict) else s.name for s in found]


@pytest.mark.parametrize("role", [r.value for r in AgentRole])
def test_every_role_boots_with_skills(
    backend: CompositeBackend, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    monkeypatch.setattr(agent_loop, "SKILLS_ROOT", str(REPO / "skills"))
    loaded = [n for source in agent_loop._skill_sources(role) for n in _names(backend, source)]
    assert loaded, f"{role} booted with no skills"


def test_base_skills_reach_everyone(backend: CompositeBackend) -> None:
    """Every role gets the base skills, whatever else it has."""
    assert set(_names(backend, "/skills/base")) == {
        "working-in-the-workspace",
        "writing-a-ticket",
    }


def test_ls_paths_survive_the_mount(backend: CompositeBackend) -> None:
    """The bug itself: paths coming back through the route must be intact."""
    entries = backend.ls("/skills/base").entries
    assert entries, "nothing under /skills/base"
    for entry in entries:
        assert entry["path"].startswith("/skills/"), (
            f"mount prefix mangled: {entry['path']} — is the route registered "
            "with its trailing slash?"
        )


def test_a_role_cannot_see_another_roles_playbook() -> None:
    """Scoping by construction · the EM's skills are not in anyone else's list."""
    em = agent_loop._skill_sources("engineering_manager")
    backend_eng = agent_loop._skill_sources("backend_engineer")
    assert not set(em) & set(backend_eng) - {"/skills/base"}
