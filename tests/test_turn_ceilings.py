"""What one turn of each role may cost.

The prompts ask every role to work in a single pass. These are what happens
when one does not — a prompt is negotiable under pressure, a ceiling is not.
Pinned in a test because the numbers are the difference between a loop that
stops and a prepaid balance that does not.
"""

from __future__ import annotations

from agents.shared.agent_loop import CALLS_PER_TURN, MAX_MODEL_CALLS_PER_TURN, calls_per_turn
from apps.control_plane.domain.caps import PER_PROJECT_LIMITS, AgentRole


def test_every_spawnable_role_has_a_ceiling() -> None:
    """A role the crew can spawn must not fall back to the loose default."""
    spawnable = [r.value for r in AgentRole if PER_PROJECT_LIMITS[r] > 0]
    missing = [role for role in spawnable if role not in CALLS_PER_TURN]
    assert not missing, f"no per-turn ceiling for {missing}"


def test_one_pass_roles_are_the_cheapest() -> None:
    """The PM writes one spec and QA posts one verdict · neither should cost
    what a coordinating EM costs."""
    assert calls_per_turn("product_manager") < calls_per_turn("engineering_manager")
    assert calls_per_turn("qa_engineer") < calls_per_turn("engineering_manager")
    assert calls_per_turn("qa_engineer") <= calls_per_turn("backend_engineer")


def test_no_ceiling_exceeds_the_default() -> None:
    """Every role was tightened from 40, none loosened past it."""
    assert all(limit <= MAX_MODEL_CALLS_PER_TURN for limit in CALLS_PER_TURN.values())


def test_an_unknown_role_still_gets_a_ceiling() -> None:
    assert calls_per_turn("devops") == MAX_MODEL_CALLS_PER_TURN
