"""The point of the split: these run with no cluster, no Docker, no network."""

import pytest
from apps.control_plane.domain.caps import (
    GLOBAL_LIMITS,
    MAX_CONCURRENT_PROJECTS,
    PER_PROJECT_LIMITS,
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
    """Derived from the limit tables, never from copies of their numbers.

    These once hardcoded 4 and 12. Tightening the caps after a runaway project
    broke eleven tests that were only restating the constants, which is noise
    pretending to be coverage — the behaviour had not changed at all.
    """

    ROLE = AgentRole.BACKEND_ENGINEER
    PROJECT = PER_PROJECT_LIMITS[ROLE]
    GLOBAL = GLOBAL_LIMITS[ROLE]

    def test_allows_below_both_ceilings(self) -> None:
        assert check_spawn(Census(self.ROLE, self.PROJECT - 1, self.GLOBAL - 1)) is None

    def test_refuses_at_project_ceiling(self) -> None:
        refusal = check_spawn(Census(self.ROLE, self.PROJECT, self.GLOBAL - 1))
        assert refusal is not None
        assert refusal.scope == "project"
        assert "Do not retry" in refusal.message

    def test_override_lifts_the_project_ceiling(self) -> None:
        census = Census(self.ROLE, self.PROJECT, self.GLOBAL - 1)
        assert check_spawn(census, override=True) is None

    def test_global_ceiling_is_never_overridable(self) -> None:
        """Founder approval cannot create more cluster."""
        refusal = check_spawn(Census(self.ROLE, 0, self.GLOBAL), override=True)
        assert refusal is not None
        assert refusal.scope == "global"

    def test_global_checked_before_project(self) -> None:
        refusal = check_spawn(Census(self.ROLE, self.PROJECT, self.GLOBAL))
        assert refusal is not None and refusal.scope == "global"


class TestRolesTheCrewDoesNotHave:
    """A limit of zero is not crowding · it is "we do not use that role".

    Reviewers and QA are what made a project unbounded: each review comment is
    an event the engineer answers, and each answer is another event to review.
    """

    def test_a_zero_limit_refuses_immediately(self) -> None:
        refusal = check_spawn(Census(AgentRole.CODE_REVIEWER, 0, 0))
        assert refusal is not None
        assert refusal.scope == "role"
        assert "no code_reviewer role" in refusal.message

    def test_and_an_override_cannot_lift_it(self) -> None:
        refusal = check_spawn(Census(AgentRole.CODE_REVIEWER, 0, 0), override=True)
        assert refusal is not None and refusal.limit == 0

    def test_qa_is_allowed_exactly_one_round(self) -> None:
        """QA checks · the prompt stops it checking twice, this stops a second
        QA agent existing to check on behalf of the first."""
        assert PER_PROJECT_LIMITS[AgentRole.QA_ENGINEER] == 1
        assert check_spawn(Census(AgentRole.QA_ENGINEER, 0, 0)) is None
        assert check_spawn(Census(AgentRole.QA_ENGINEER, 1, 1)) is not None


class TestProjectLimits:
    def test_refuses_one_past_the_ceiling(self) -> None:
        assert check_new_project(MAX_CONCURRENT_PROJECTS - 1) is None
        assert check_new_project(MAX_CONCURRENT_PROJECTS) is not None


class TestProjectLifecycle:
    def test_cannot_start_a_finished_project(self) -> None:
        done = Project(id="p").finish()
        assert done.status is ProjectStatus.DONE
        with pytest.raises(ValueError, match="already"):
            done.start()
