"""Both runtimes answer the same contract · one of them needs no cluster."""

import pytest

from apps.control_plane.adapters.fake_runtime import FakeAgentRuntime
from apps.control_plane.adapters.k8s_runtime import KubernetesAgentRuntime, _job_name
from apps.control_plane.domain.caps import AgentRole, Census, check_spawn
from apps.control_plane.ports.runtime import (
    AgentRuntime,
    AgentSpec,
    AgentState,
    AgentStatus,
)


def test_both_adapters_satisfy_the_port() -> None:
    assert isinstance(FakeAgentRuntime(), AgentRuntime)
    assert isinstance(KubernetesAgentRuntime(), AgentRuntime)


class TestSpawnPathWithoutACluster:
    """The reason the port exists · policy tested with no infrastructure."""

    async def _spawn(self, rt: FakeAgentRuntime, role: AgentRole, project: str) -> bool:
        census = Census(role, await rt.census(role, project), await rt.census(role))
        if check_spawn(census):
            return False
        await rt.launch(AgentSpec(role=role, project_id=project))
        return True

    @pytest.mark.asyncio
    async def test_caps_hold_against_a_runtime(self) -> None:
        rt = FakeAgentRuntime()
        results = [
            await self._spawn(rt, AgentRole.BACKEND_ENGINEER, "proj-a") for _ in range(6)
        ]
        assert results == [True, True, True, True, False, False]
        assert len(rt.launched) == 4

    @pytest.mark.asyncio
    async def test_a_finished_agent_frees_its_slot(self) -> None:
        rt = FakeAgentRuntime()
        first = await rt.launch(AgentSpec(role=AgentRole.PRODUCT_MANAGER, project_id="p"))
        assert not await self._spawn(rt, AgentRole.PRODUCT_MANAGER, "p")  # cap is 1

        rt.finish(first)
        assert await self._spawn(rt, AgentRole.PRODUCT_MANAGER, "p")

    @pytest.mark.asyncio
    async def test_projects_do_not_consume_each_other_s_slots(self) -> None:
        rt = FakeAgentRuntime()
        for _ in range(4):
            await self._spawn(rt, AgentRole.BACKEND_ENGINEER, "proj-a")
        assert await self._spawn(rt, AgentRole.BACKEND_ENGINEER, "proj-b")

    @pytest.mark.asyncio
    async def test_the_global_ceiling_stops_the_third_project(self) -> None:
        """4 + 4 + 4 = 12, which is the cluster-wide limit."""
        rt = FakeAgentRuntime()
        for project in ("a", "b", "c"):
            for _ in range(4):
                await self._spawn(rt, AgentRole.BACKEND_ENGINEER, project)
        assert await rt.census(AgentRole.BACKEND_ENGINEER) == 12
        assert not await self._spawn(rt, AgentRole.BACKEND_ENGINEER, "d")


class TestJobNames:
    def test_is_dns_1123(self) -> None:
        import re

        name = _job_name(AgentRole.BACKEND_ENGINEER, "proj_A1B2C3")
        assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name), name
        assert len(name) <= 63

    def test_survives_a_long_project_id(self) -> None:
        assert len(_job_name(AgentRole.ENGINEERING_MANAGER, "x" * 200)) <= 63

    def test_two_agents_on_one_project_do_not_collide(self) -> None:
        a = _job_name(AgentRole.QA_ENGINEER, "proj-1")
        b = _job_name(AgentRole.QA_ENGINEER, "proj-1")
        assert a != b


class TestStuckDetection:
    """Kubernetes never fails these on its own, so we have to notice."""

    @pytest.mark.parametrize(
        "reason", ["CreateContainerConfigError", "ImagePullBackOff", "ErrImagePull"]
    )
    def test_flags_reasons_that_never_resolve(self, reason: str) -> None:
        assert AgentStatus(AgentState.PENDING, reason=reason).is_stuck

    @pytest.mark.parametrize("reason", ["ContainerCreating", "PodInitializing", "Retrying"])
    def test_leaves_ordinary_slowness_alone(self, reason: str) -> None:
        assert not AgentStatus(AgentState.PENDING, reason=reason).is_stuck

    def test_a_running_agent_is_never_stuck(self) -> None:
        assert not AgentStatus(AgentState.RUNNING).is_stuck
