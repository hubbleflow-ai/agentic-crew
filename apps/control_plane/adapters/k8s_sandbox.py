"""Kubernetes sandbox · agent-written code runs in a Job and then is gone.

The old implementation kept a long-lived container per project and reached it
through the host's Docker socket. Two problems with that, and the second is the
serious one: it does not work in a cluster at all, and mounting `docker.sock`
into a pod hands whatever runs there full control of the machine — including
the ability to start a privileged container. A service that runs
model-written code is the last place to put that.

Here every execution is its own Job:

* no network — `sandbox_exec` cannot call out, so a model that decides to
  `curl` something finds nothing listening
* read-only root filesystem, non-root user, every capability dropped
* the project's workspace mounted at `/workspace`, and nothing else
* a hard deadline, so a runaway loop is the cluster's problem, not ours

The cost is a few seconds of pod startup per command. That is the price of the
blast radius being one pod, and for running tests it is the right trade.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any, cast

from agents.shared.logging_setup import setup_logging
from apps.control_plane.ports.sandbox import ExecResult, SandboxBackend, SandboxHandle
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException

log = setup_logging("k8s-sandbox")

LABEL_SANDBOX = "crew.hubbleflow.ai/sandbox"
LABEL_PROJECT = "crew.hubbleflow.ai/project"

POLL_INTERVAL_S = 0.5
STARTUP_GRACE_S = 60
"""How long to wait for a pod to exist before giving up on it."""


class KubernetesSandbox(SandboxBackend):
    """One Job per command. Satisfies the SandboxBackend port."""

    def __init__(
        self,
        *,
        namespace: str = "crew",
        image: str = "crew-base:dev",
        workspace_claim: str = "crew-workspace",
        service_account: str = "crew-agent",
    ) -> None:
        self.namespace = namespace
        self.image = image
        self.workspace_claim = workspace_claim
        self.service_account = service_account
        self._client: client.ApiClient | None = None
        self._batch: client.BatchV1Api | None = None
        self._core: client.CoreV1Api | None = None

    async def connect(self) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()
        self._client = client.ApiClient()
        self._batch = client.BatchV1Api(self._client)
        self._core = client.CoreV1Api(self._client)
        log.info("sandbox.ready namespace=%s image=%s", self.namespace, self.image)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
        self._client = self._batch = self._core = None

    async def create(self, project_id: str) -> SandboxHandle:
        """No-op · a sandbox here is per-command, not per-project.

        The handle exists so callers can hold onto a project without caring
        whether the backend is long-lived or not.
        """
        return SandboxHandle(id=f"sandbox-{project_id}", project_id=project_id)

    async def exec(
        self, handle: SandboxHandle, command: list[str], *, timeout_s: int = 120
    ) -> ExecResult:
        """Run one command in its own Job and wait for it."""
        assert self._batch and self._core, "connect() was never awaited"
        name = f"sbx-{handle.project_id[:12]}-{secrets.token_hex(3)}".replace("_", "-")

        await self._batch.create_namespaced_job(
            namespace=self.namespace,
            body=cast("Any", self._manifest(name, handle.project_id, command, timeout_s)),
        )
        log.info("sandbox.exec job=%s argv=%s", name, command[:3])

        try:
            return await self._await_result(name, timeout_s)
        finally:
            # Delete eagerly rather than waiting for a TTL · a test suite that
            # runs a command a second leaves a lot of Jobs behind otherwise.
            await self._delete(name)

    async def destroy(self, handle: SandboxHandle) -> None:
        """Remove anything still running for this project."""
        assert self._batch, "connect() was never awaited"
        await self._batch.delete_collection_namespaced_job(
            namespace=self.namespace,
            label_selector=f"{LABEL_SANDBOX}=true,{LABEL_PROJECT}={handle.project_id}",
            propagation_policy="Background",
        )

    # ─── internals ───────────────────────────────────────────────────────

    async def _await_result(self, name: str, timeout_s: int) -> ExecResult:
        assert self._batch and self._core
        deadline = timeout_s + STARTUP_GRACE_S
        waited = 0.0

        while waited < deadline:
            job = await self._batch.read_namespaced_job_status(
                name=name, namespace=self.namespace
            )
            if job.status.succeeded or job.status.failed:
                return await self._collect(name, failed=bool(job.status.failed))
            await asyncio.sleep(POLL_INTERVAL_S)
            waited += POLL_INTERVAL_S

        return ExecResult(
            exit_code=124,
            stdout=await self._logs(name),
            stderr=f"sandbox exceeded {timeout_s}s and was stopped",
            timed_out=True,
        )

    async def _collect(self, name: str, *, failed: bool) -> ExecResult:
        """Read the exit code from the pod, and the output from its log.

        Kubernetes gives no way to separate stdout from stderr after the fact ·
        a pod's log is the merged stream. Rather than pretend, the merged
        output goes in ``stdout`` and ``stderr`` carries only our own message.
        """
        assert self._core
        output = await self._logs(name)
        pods = await self._core.list_namespaced_pod(
            namespace=self.namespace, label_selector=f"job-name={name}"
        )
        exit_code = 1 if failed else 0
        for pod in pods.items:
            for cs in pod.status.container_statuses or []:
                if cs.state.terminated:
                    exit_code = cs.state.terminated.exit_code
        return ExecResult(exit_code=exit_code, stdout=output, stderr="")

    async def _logs(self, name: str) -> str:
        assert self._core
        pods = await self._core.list_namespaced_pod(
            namespace=self.namespace, label_selector=f"job-name={name}"
        )
        if not pods.items:
            return ""
        try:
            result: str = await self._core.read_namespaced_pod_log(
                name=pods.items[0].metadata.name, namespace=self.namespace
            )
        except ApiException:
            return ""
        return result

    async def _delete(self, name: str) -> None:
        assert self._batch
        try:
            await self._batch.delete_namespaced_job(
                name=name, namespace=self.namespace, propagation_policy="Background"
            )
        except ApiException as exc:
            if exc.status != 404:
                raise

    def _manifest(
        self, name: str, project_id: str, command: list[str], timeout_s: int
    ) -> dict[str, Any]:
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "labels": {LABEL_SANDBOX: "true", LABEL_PROJECT: project_id},
            },
            "spec": {
                # No retries. Re-running a failed test suite would report a
                # different answer than the one the agent asked for.
                "backoffLimit": 0,
                "activeDeadlineSeconds": timeout_s,
                "ttlSecondsAfterFinished": 60,
                "template": {
                    "metadata": {"labels": {LABEL_SANDBOX: "true"}},
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": self.service_account,
                        # The single most important line in this file.
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "fsGroup": 1000,
                        },
                        "containers": [
                            {
                                "name": "sandbox",
                                "image": self.image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": command,
                                "workingDir": "/workspace",
                                "volumeMounts": [
                                    {
                                        "name": "workspace",
                                        "mountPath": "/workspace",
                                        "subPath": project_id,
                                    },
                                    # The root filesystem is read-only, so
                                    # anything that needs to write gets an
                                    # explicit, disposable place to do it.
                                    {"name": "tmp", "mountPath": "/tmp"},
                                ],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "256Mi"},
                                    "limits": {"cpu": "1", "memory": "1Gi"},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                            }
                        ],
                        "volumes": [
                            {
                                "name": "workspace",
                                "persistentVolumeClaim": {"claimName": self.workspace_claim},
                            },
                            {"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}},
                        ],
                    },
                },
            },
        }
