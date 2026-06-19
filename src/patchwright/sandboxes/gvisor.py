"""GVisorSandbox — hardened backend via `docker run --runtime=runsc` (AEG-461).

Layers three NFR-S enforcement gates on top of the M3-shim Docker dev backend:

  NFR-S-1  Application-kernel isolation       runsc OCI runtime
  NFR-S-2  Network default-deny               --network=none (allowlist deferred to .3 + iptables)
  NFR-S-3  Filesystem default-read-only       --read-only + Mount.readonly default + tmpfs /tmp

The hardened backend is Linux-only — `runsc` does not run on macOS. `is_available()`
returns False on macOS so callers fall back to `DockerSandbox` cleanly. The agent
tests stub `SandboxRunner` regardless of host; the real-PoC e2e tests + T6
negative-test suite ride in M3-hard.3 (AEG-463) and skip when runsc isn't around.

Why subprocess + Docker (rather than direct `runsc run`):
- Docker has the OCI bundle, image cache, cidfile/cgroup integration the dev box
  already trusts. `--runtime=runsc` just selects a different runtime under the
  same plumbing.
- Reuses the well-tested DockerSandbox subprocess helpers — no duplicate stream
  reader, timeout handler, or container-kill plumbing.

T6 (sandbox escape) mitigation lands cumulatively: this PR enforces the
deny-by-default policy via argv; the actual `runsc cannot egress / cannot write
outside scratch` negative tests live in AEG-463.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from patchwright.core.sandbox import (
    Mount,
    NetworkPolicy,
    ResourceLimits,
    RunResult,
    SandboxError,
)
from patchwright.sandboxes.docker import (
    _DEFAULT_MAX_OUTPUT_BYTES,
    _run_popen,
    _validate_env_key,
    _validate_mount,
)

_DEFAULT_TIMEOUT_SECONDS = 60.0
_AVAILABILITY_TIMEOUT = 10.0
_DEFAULT_TMPFS_SIZE = "64m"
_RUNSC_RUNTIME_NAME = "runsc"


@dataclass
class GVisorSandbox:
    """Hardened backend. `docker run --runtime=runsc --read-only` + tmpfs /tmp.

    Defaults enforce the deny-by-default posture: network=none, mounts
    readonly, rootfs readonly. Callers explicitly opt in to writable scratch
    via `Mount(..., readonly=False)`.

    Mirrors `DockerSandbox` interface; the registry can DI either one
    interchangeably behind the `SandboxRunner` Protocol.
    """

    name: str = "gvisor"
    docker_binary: str = "docker"
    runsc_binary: str = "runsc"
    """Path to the gVisor runtime. Override for tests / non-standard installs."""

    runtime_name: str = _RUNSC_RUNTIME_NAME
    """Name Docker uses to look up the runsc runtime in its config.
    `dockerd --add-runtime runsc=/usr/bin/runsc` registers it under this name."""

    memory: str = "256m"
    pids_limit: int = 64
    cpus: float | None = None
    """Default CPU cap. None = inherit host. Per-call override via ResourceLimits."""

    tmpfs_size: str = _DEFAULT_TMPFS_SIZE
    """Size of the writable tmpfs mounted at /tmp. The container's rootfs is
    --read-only, so most package-manager / log-emitting workloads need a small
    tmpfs to function at all."""

    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES

    _availability_cache: bool | None = field(default=None, init=False, repr=False)

    # ----------------------------------------------------------------- availability

    def is_available(self) -> bool:
        """True iff:
          - `runsc --version` exits 0 (gVisor binary installed), AND
          - `docker info` reports the runsc runtime registered.
        Cached after the first call.
        """
        if self._availability_cache is not None:
            return self._availability_cache

        self._availability_cache = self._check_availability()
        return self._availability_cache

    def _check_availability(self) -> bool:  # noqa: PLR0911 — one return per guard step
        if shutil.which(self.runsc_binary) is None:
            return False
        if shutil.which(self.docker_binary) is None:
            return False

        try:
            runsc_check = subprocess.run(
                [self.runsc_binary, "--version"],
                capture_output=True,
                check=False,
                timeout=_AVAILABILITY_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if runsc_check.returncode != 0:
            return False

        try:
            info = subprocess.run(
                [self.docker_binary, "info", "--format", "{{json .Runtimes}}"],
                capture_output=True,
                check=False,
                timeout=_AVAILABILITY_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if info.returncode != 0:
            return False

        return self._runtime_registered(info.stdout)

    def _runtime_registered(self, info_stdout: bytes) -> bool:
        """docker info --format '{{json .Runtimes}}' prints {"runc": {...}, "runsc": {...}}.
        Tolerate a missing / malformed payload — return False rather than raising."""
        try:
            runtimes = json.loads(info_stdout.decode("utf-8", errors="replace") or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(runtimes, dict) and self.runtime_name in runtimes

    # ----------------------------------------------------------------- argv builder

    def _build_args(
        self,
        *,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None,
        env: dict[str, str] | None,
        policy: NetworkPolicy,
        eff_memory: str,
        eff_pids: int,
        eff_cpus: float | None,
        cid_path: str,
    ) -> list[str]:
        """Build docker argv with gVisor runtime + hardened defaults.

        The argv shape is the load-bearing API of this module — the tests
        introspect it directly to assert the deny-by-default policy without
        ever invoking a real container.
        """
        args = [
            self.docker_binary,
            "run",
            "--rm",
            f"--runtime={self.runtime_name}",
            "--read-only",  # NFR-S-3: rootfs is RO by default
            f"--tmpfs=/tmp:rw,noexec,nosuid,size={self.tmpfs_size}",
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
        ]

        if policy.mode in ("none", "allowlist"):
            # TODO(M3-hard.3): allowlist mode lands with the iptables / per-case
            # firewall plumbing. For .1 it collapses to deny — the type-stable
            # NetworkPolicy.mode lets that .3 work be a value change, not an API
            # change.
            args += ["--network=none"]
        elif policy.mode == "bridge":
            # Explicit opt-in for the rare repro that needs network. The
            # operator is acknowledging the T6 risk surface widens.
            pass  # default Docker bridge

        args += [f"--memory={eff_memory}", f"--pids-limit={eff_pids}"]
        if eff_cpus is not None:
            args += [f"--cpus={eff_cpus}"]

        args += ["--user=nobody"]

        for k, v in (env or {}).items():
            _validate_env_key(k)
            args += ["-e", f"{k}={v}"]

        for m in mounts or []:
            resolved = _validate_mount(m)
            ro = ":ro" if m.readonly else ""
            args += ["-v", f"{resolved}:{m.target}{ro}"]

        args += [f"--cidfile={cid_path}", "--", image, *cmd]
        return args

    # ----------------------------------------------------------------- run

    def run(
        self,
        *,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        network_policy: NetworkPolicy | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> RunResult:
        """Execute `cmd` inside `image` under gVisor.

        Per the Protocol contract, never raises for non-zero exits or timeouts —
        each terminal condition is returned as a RunResult so the reproduce
        agent can journal it. Only `is_available() == False` causes a raise
        (SandboxError), because the agent has no useful outcome to record.
        """
        if not self.is_available():
            raise SandboxError(
                f"gvisor backend not available (runsc={self.runsc_binary!r} not "
                f"installed or not registered as a Docker runtime named "
                f"{self.runtime_name!r})"
            )

        policy = network_policy or NetworkPolicy()
        limits = resource_limits or ResourceLimits()

        eff_memory = limits.memory if limits.memory is not None else self.memory
        eff_pids = limits.pids_limit if limits.pids_limit is not None else self.pids_limit
        eff_cpus = limits.cpus if limits.cpus is not None else self.cpus

        # cidfile must not exist when --cidfile points at it.
        cid_fd, cid_path = tempfile.mkstemp(suffix=".cid")
        os.close(cid_fd)
        os.unlink(cid_path)

        args = self._build_args(
            image=image,
            cmd=cmd,
            mounts=mounts,
            env=env,
            policy=policy,
            eff_memory=eff_memory,
            eff_pids=eff_pids,
            eff_cpus=eff_cpus,
            cid_path=cid_path,
        )

        network_enabled = policy.mode == "bridge"

        try:
            return _run_popen(
                args=args,
                image=image,
                cmd=cmd,
                env=env,
                timeout=timeout,
                network_enabled=network_enabled,
                max_output_bytes=self.max_output_bytes,
                docker_binary=self.docker_binary,
                cid_path=cid_path,
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(cid_path)


__all__ = ["GVisorSandbox"]
