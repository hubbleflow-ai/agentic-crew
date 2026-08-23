"""Kubernetes adapter · agents are Jobs, and the cluster owns their lives.

This is the whole point of moving off ``docker run``. The control plane makes
**one** API call to create a Job and then stops being responsible: scheduling,
image pull, retry on crash, marking completion, and deleting the leftovers are
the cluster's job, done by controllers that keep working while this process is
restarting.

A Job, not a bare Pod. A bare Pod that dies stays dead unless something is
watching it — and that something would be us, re-implementing a controller in
application code. ``backoffLimit`` gives retries; ``ttlSecondsAfterFinished``
gives cleanup. Neither costs us a line of lifecycle management.

Census comes from a label selector over the cluster, never from a counter in
memory. That is what makes the spawn caps in :mod:`domain.caps` survive a
control-plane restart — the old in-process dictionary silently reset to zero
and let the founder spawn past the ceiling.
"""

from __future__ import annotations

import secrets
from typing import Any, cast

from agents.shared.logging_setup import setup_logging
from apps.control_plane.domain.caps import AgentRole
from apps.control_plane.ports.runtime import (
    AgentHandle,
    AgentSpec,
    AgentState,
    AgentStatus,
)
from contracts import agent_env
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException

log = setup_logging("k8s-runtime")

# Labels are the API. Everything that finds, counts, or cleans up agents does
# it through these, so they are constants rather than strings typed twice.
LABEL_MANAGED = "crew.hubbleflow.ai/managed-by"
LABEL_ROLE = "crew.hubbleflow.ai/role"
LABEL_PROJECT = "crew.hubbleflow.ai/project"

MANAGED_BY = "control-plane"

BACKOFF_LIMIT = 2
"""Two retries. A harness that fails three times has a real problem, and
retrying it forever just burns quota against the same bad prompt."""

TTL_AFTER_FINISHED_S = 300
"""Finished Jobs linger five minutes so logs stay fetchable, then the cluster
deletes them. Without this, a day of demos leaves hundreds of dead objects."""

ACTIVE_DEADLINE_S = 3600
"""An agent that has not finished in an hour is stuck, not thorough."""


class KubernetesAgentRuntime:
    """Runs crew agents as Kubernetes Jobs.

    Satisfies :class:`~apps.control_plane.ports.runtime.AgentRuntime`.
    """

    def __init__(
        self,
        *,
        namespace: str = "crew",
        image: str = "crew-agent:dev",
        image_pull_policy: str = "IfNotPresent",
        secret_name: str = "crew-secrets",
        config_map_name: str = "crew-config",
        workspace_claim: str = "crew-workspace",
        service_account: str = "crew-agent",
    ) -> None:
        self.namespace = namespace
        self.image = image
        self.image_pull_policy = image_pull_policy
        self.secret_name = secret_name
        self.config_map_name = config_map_name
        self.workspace_claim = workspace_claim
        self.service_account = service_account
        self._client: client.ApiClient | None = None
        self._api: client.BatchV1Api | None = None
        self._core: client.CoreV1Api | None = None

    # ─── connection ──────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Load credentials · in-cluster when we are a pod, kubeconfig locally.

        Both paths are needed: the control plane runs inside the cluster in a
        real deploy, and on a laptop against minikube while teaching.
        """
        try:
            config.load_incluster_config()
            log.info("k8s.config source=in-cluster namespace=%s", self.namespace)
        except config.ConfigException:
            await config.load_kube_config()
            log.info("k8s.config source=kubeconfig namespace=%s", self.namespace)

        # One HTTP session shared by both APIs · two ApiClients would mean two
        # connection pools to the same API server, and two things to close.
        self._client = client.ApiClient()
        self._api = client.BatchV1Api(self._client)
        self._core = client.CoreV1Api(self._client)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
        self._client = self._api = self._core = None

    @property
    def batch(self) -> client.BatchV1Api:
        if self._api is None:
            raise RuntimeError("KubernetesAgentRuntime.connect() was never awaited")
        return self._api

    @property
    def core(self) -> client.CoreV1Api:
        if self._core is None:
            raise RuntimeError("KubernetesAgentRuntime.connect() was never awaited")
        return self._core

    # ─── the port ────────────────────────────────────────────────────────

    async def launch(self, spec: AgentSpec) -> AgentHandle:
        """Create a Job and return immediately.

        Nothing here waits for the pod to be scheduled, pull its image, or
        become ready. Kubernetes will do all of that whether or not this
        process is still alive.
        """
        name = _job_name(spec.role, spec.project_id)
        body = self._manifest(name, spec)

        try:
            # The stub asks for a V1Job model; the client serialises a dict
            # unchanged, and a dict is what the notebooks put beside
            # `kubectl get job -o yaml`. Keep the dict, narrow the type here.
            await self.batch.create_namespaced_job(
                namespace=self.namespace, body=cast("Any", body)
            )
        except ApiException as exc:
            log.error(
                "k8s.launch_failed role=%s project_id=%s status=%s reason=%s",
                spec.role,
                spec.project_id,
                exc.status,
                exc.reason,
            )
            raise

        log.info("k8s.launched job=%s role=%s project_id=%s", name, spec.role, spec.project_id)
        return AgentHandle(id=name, name=name, role=spec.role, project_id=spec.project_id)

    async def status(self, handle: AgentHandle) -> AgentStatus:
        """Read the Job, then the pod behind it.

        The Job alone is not enough. ``status.active`` counts a pod that has
        been created but cannot start — a missing Secret, an image that will
        not pull — so a Job-only reading calls a permanently broken agent
        "running" and the founder waits for output that is never coming. The
        pod knows the difference, so we ask it.
        """
        try:
            job = await self.batch.read_namespaced_job_status(
                name=handle.name, namespace=self.namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                # Already collected by the TTL controller · a Job only reaches
                # TTL after finishing, so "gone" means "done".
                return AgentStatus(AgentState.SUCCEEDED, reason="Collected")
            raise

        if job.status.succeeded:
            return AgentStatus(AgentState.SUCCEEDED)
        if job.status.conditions:
            for cond in job.status.conditions:
                if cond.type == "Failed" and cond.status == "True":
                    return AgentStatus(
                        AgentState.FAILED,
                        reason=cond.reason or "Failed",
                        detail=cond.message or "",
                    )
        if not job.status.active:
            return AgentStatus(AgentState.PENDING, reason="NoPodYet")

        return await self._pod_status(handle)


    async def _pod_status(self, handle: AgentHandle) -> AgentStatus:
        """Turn the newest pod's condition into a state plus a reason.

        Only reached while the Job is still active, which is why a crashed pod
        here means "retrying", not "failed" · the Job controller owns that
        verdict and :meth:`status` has already checked for it.
        """
        pod = await self._newest_pod(handle)
        if pod is None:
            return AgentStatus(AgentState.PENDING, reason="NoPodYet")

        statuses = pod.status.container_statuses or []
        for cs in statuses:
            if cs.state.waiting:
                # Waiting is where the interesting failures live · this is the
                # reason `is_stuck` needs to tell a slow start from a dead one.
                return AgentStatus(
                    AgentState.PENDING,
                    reason=cs.state.waiting.reason or "Waiting",
                    detail=cs.state.waiting.message or "",
                )
            if cs.state.running:
                return AgentStatus(AgentState.RUNNING)
            if cs.state.terminated:
                # The Job's `active` count lags the container by a second or
                # two, so we get here for a container that has already exited.
                # Its own exit code is the truth · without this branch a
                # finished agent briefly reads as pending, which looks like a
                # hang to anyone watching.
                term = cs.state.terminated
                if term.exit_code == 0:
                    return AgentStatus(AgentState.SUCCEEDED)
                # Crashed, but the Job still has retries · calling this FAILED
                # would let a caller give up while Kubernetes is already
                # starting the replacement. The Job's Failed condition, checked
                # in `status`, is the only thing that ends an agent for good.
                return AgentStatus(
                    AgentState.PENDING,
                    reason="Retrying",
                    detail=f"attempt exited {term.exit_code}",
                )

        return AgentStatus(AgentState.PENDING, reason=pod.status.phase or "Pending")

    async def _newest_pod(self, handle: AgentHandle) -> Any | None:
        """The most recent pod a Job created · retries make older ones stale."""
        pods = await self.core.list_namespaced_pod(
            namespace=self.namespace, label_selector=f"job-name={handle.name}"
        )
        if not pods.items:
            return None
        return max(pods.items, key=lambda p: p.metadata.creation_timestamp)

    async def census(self, role: AgentRole, project_id: str | None = None) -> int:
        """Count agents of a role that are still alive, asked of the cluster.

        Only ``active`` Jobs count. A finished agent occupies no slot, and the
        founder should be able to spawn another the moment one completes.
        """
        return len(await self._live_jobs(role, project_id))

    async def handles(self, role: AgentRole, project_id: str) -> list[AgentHandle]:
        """The live agents of a role on one project."""
        return [
            AgentHandle(
                id=job.metadata.name,
                name=job.metadata.name,
                role=role,
                project_id=project_id,
            )
            for job in await self._live_jobs(role, project_id)
        ]

    async def _live_jobs(self, role: AgentRole, project_id: str | None) -> list[Any]:
        """Jobs of a role that have not finished · the one place the selector
        is built, so counting and listing can never disagree."""
        selector = f"{LABEL_MANAGED}={MANAGED_BY},{LABEL_ROLE}={role.value}"
        if project_id:
            selector += f",{LABEL_PROJECT}={project_id}"

        jobs = await self.batch.list_namespaced_job(
            namespace=self.namespace, label_selector=selector
        )
        return [job for job in jobs.items if job.status.active]

    async def logs(self, handle: AgentHandle, *, tail: int = 200) -> str:
        """Fetch the agent's stdout, via the pod the Job created."""
        pod = await self._newest_pod(handle)
        if pod is None:
            return ""
        try:
            result: str = await self.core.read_namespaced_pod_log(
                name=pod.metadata.name, namespace=self.namespace, tail_lines=tail
            )
        except ApiException as exc:
            if exc.status in (400, 404):
                return ""  # not started yet, or already collected
            raise
        return result

    # ─── manifest ────────────────────────────────────────────────────────

    def _manifest(self, name: str, spec: AgentSpec) -> dict[str, Any]:
        """Build the Job.

        Written as a plain dict rather than the client's model classes: this
        is the object the notebooks show beside a ``kubectl get job -o yaml``,
        and the two should look like the same thing.
        """
        labels = {
            LABEL_MANAGED: MANAGED_BY,
            LABEL_ROLE: spec.role.value,
            LABEL_PROJECT: spec.project_id,
        }

        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": name, "labels": labels},
            "spec": {
                "backoffLimit": BACKOFF_LIMIT,
                "ttlSecondsAfterFinished": TTL_AFTER_FINISHED_S,
                "activeDeadlineSeconds": ACTIVE_DEADLINE_S,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": self.service_account,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "fsGroup": 1000,
                        },
                        "containers": [
                            {
                                "name": "agent",
                                "image": self.image,
                                "imagePullPolicy": self.image_pull_policy,
                                "env": [
                                    {"name": agent_env.ROLE, "value": spec.role.value},
                                    {"name": agent_env.PROJECT_ID, "value": spec.project_id},
                                    {"name": agent_env.AGENT_NAME, "value": name},
                                    {"name": agent_env.ASSIGNMENT, "value": spec.assignment},
                                ],
                                # Keys and endpoints arrive from cluster objects,
                                # never copied out of the control plane's own env.
                                "envFrom": [
                                    {"secretRef": {"name": self.secret_name}},
                                    {"configMapRef": {"name": self.config_map_name}},
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "workspace",
                                        "mountPath": agent_env.WORKSPACE,
                                        "subPath": spec.project_id,
                                    }
                                ],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "256Mi"},
                                    "limits": {"cpu": "1", "memory": "1Gi"},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                            }
                        ],
                        "volumes": [
                            {
                                "name": "workspace",
                                "persistentVolumeClaim": {"claimName": self.workspace_claim},
                            }
                        ],
                    },
                },
            },
        }


def _job_name(role: AgentRole, project_id: str) -> str:
    """A DNS-1123 name that reads well in ``kubectl get jobs``.

    Underscores are legal in Python enums and illegal in object names, so the
    role is hyphenated. The random suffix keeps a second engineer on the same
    project from colliding with the first.
    """
    role_part = role.value.replace("_", "-")
    project_part = project_id.replace("_", "-").lower()[:16].strip("-")
    return f"{role_part}-{project_part}-{secrets.token_hex(3)}"[:63]
