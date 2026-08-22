"""The point of the split: these run with no cluster, no Docker, no network."""

import pytest
from apps.control_plane.domain.caps import (
    AgentRole,
    Census,
    check_new_project,
    check_spawn,
)
from apps.control_plane.domain.project import (
    PROVISIONAL_NAME,
    NamingError,
    Project,
    ProjectStatus,
)


class TestProjectNaming:
    def test_starts_provisional(self) -> None:
        p = Project(id="proj-1")
        assert p.name == PROVISIONAL_NAME
        assert not p.is_named

    def test_rename_marks_it_named(self) -> None:
        p = Project(id="proj-1").rename("Healthz endpoint")
        assert p.name == "Healthz endpoint"
        assert p.is_named

    def test_rejects_the_placeholder_as_a_name(self) -> None:
        with pytest.raises(NamingError, match="placeholder"):
            Project(id="proj-1").rename("new project")

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
    def test_rejects_empty(self, bad: str) -> None:
        with pytest.raises(NamingError, match="empty"):
            Project(id="proj-1").rename(bad)

    def test_collapses_whitespace(self) -> None:
        assert Project(id="p").rename("  Add   healthz\n endpoint ").name == (
            "Add healthz endpoint"
        )

    def test_rename_does_not_mutate(self) -> None:
        original = Project(id="proj-1")
        original.rename("Something")
        assert original.name == PROVISIONAL_NAME


class TestSpawnCaps:
    def test_allows_below_both_ceilings(self) -> None:
        assert check_spawn(Census(AgentRole.BACKEND_ENGINEER, 2, 5)) is None

    def test_refuses_at_project_ceiling(self) -> None:
        refusal = check_spawn(Census(AgentRole.BACKEND_ENGINEER, 4, 5))
        assert refusal is not None
        assert refusal.scope == "project"
        assert "Do not retry" in refusal.message

    def test_override_lifts_the_project_ceiling(self) -> None:
        assert check_spawn(Census(AgentRole.BACKEND_ENGINEER, 4, 5), override=True) is None

    def test_global_ceiling_is_never_overridable(self) -> None:
        """Founder approval cannot create more cluster."""
        refusal = check_spawn(Census(AgentRole.BACKEND_ENGINEER, 0, 12), override=True)
        assert refusal is not None
        assert refusal.scope == "global"

    def test_global_checked_before_project(self) -> None:
        refusal = check_spawn(Census(AgentRole.BACKEND_ENGINEER, 4, 12))
        assert refusal is not None and refusal.scope == "global"


class TestProjectLimits:
    def test_refuses_a_fourth_project(self) -> None:
        assert check_new_project(2) is None
        assert check_new_project(3) is not None


class TestProjectLifecycle:
    def test_cannot_start_a_finished_project(self) -> None:
        done = Project(id="p").finish()
        assert done.status is ProjectStatus.DONE
        with pytest.raises(ValueError, match="already"):
            done.start()
