"""SandboxRunner Protocol — the boundary between agents and isolated execution.

The reproduce and patch_test agents call into this Protocol to run arbitrary
code (PoCs, generated tests) without trusting it to behave. The Docker
backend (sandboxes/docker.py) is the dev surface; M3-hard (Wave B) layers
gVisor + per-case network allowlist + read-only FS on top of the same
Protocol for production use.

PRD §10.1 commitment: sub-agents (especially reproduction and patch-
application) run in isolated sandboxes. The Protocol exists so the agent
code is portable across backends — swapping gVisor in for Docker is a
DI change, not a rewrite.

Design rules:
- network_policy defaults to mode="none". Per NFR-S-2 ("network policy
  default-deny"). Operators wanting egress must pass a NetworkPolicy
  explicitly.
- Mounts default to read-only. Per NFR-S-3 ("filesystem default-read-only
  outside an explicit case scratch directory").
- timeout is required and enforced — no unbounded runs. A timeout returns
  a RunResult with timed_out=True rather than raising, so callers can journal
  the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class SandboxError(Exception):
    """Raised when the sandbox cannot be invoked at all (missing backend,
    bad image, etc.). Per-run failures are returned as RunResult, not raised."""


@dataclass(frozen=True)
class Mount:
    """One bind mount from host into the container.

    Default readonly=True per NFR-S-3. Callers that need writable scratch
    must opt in explicitly.
    """

    source: Path
    """Host-side path. Must exist before run() is called."""

    target: str
    """Container-side absolute path."""

    readonly: bool = True


class ResourceLimits(BaseModel):
    """Per-call resource overrides. None fields fall back to backend defaults.

    M3-hard wires cpus per-case; the dev backend ignores it (uses --memory /
    --pids-limit only).
    """

    model_config = ConfigDict(frozen=True)

    memory: str | None = None
    pids_limit: int | None = None
    cpus: float | None = None


class NetworkPolicy(BaseModel):
    """Network access policy for one sandbox run.

    mode="allowlist" is defined here for type stability so M3-hard can slot in
    without a breaking change; the dev backend falls back to none when it
    receives mode="allowlist" (see TODO in DockerSandbox).
    """

    model_config = ConfigDict(frozen=True)

    mode: Literal["none", "bridge", "allowlist"] = "none"
    allowlist: list[str] = []


class RunResult(BaseModel):
    """Outcome of one sandboxed execution.

    Returned (not raised) for every terminal condition the sandbox can
    distinguish — exit code, timeout, killed-by-signal. Agents journal
    this verbatim as the repro_log artifact.
    """

    model_config = ConfigDict(frozen=True)

    exit_code: int
    """Process exit code. -1 if killed by signal or timed out."""

    stdout: str
    stderr: str
    timed_out: bool
    image: str
    cmd: tuple[str, ...]
    env: dict[str, str] = {}
    """Snapshot of the env vars passed *into* the sandbox. Does NOT include
    the container's full environment (which is opaque to the host)."""

    network_enabled: bool = False
    """True iff network egress was permitted for this run."""

    truncated: bool = False
    """True iff stdout or stderr was capped at max_output_bytes."""


@runtime_checkable
class SandboxRunner(Protocol):
    """A backend that can run a command in an isolated environment.

    Implementations:
      - sandboxes/docker.py     (this PR — dev backend)
      - sandboxes/gvisor.py     (M3-hard, Wave B — hardened backend)
    """

    name: str
    """Stable identifier — recorded in repro_log artifacts."""

    def run(
        self,
        *,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        network_policy: NetworkPolicy | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> RunResult: ...

    def is_available(self) -> bool:
        """True iff the backend can actually be invoked on this host
        (e.g., docker daemon reachable). Callers use this to skip the
        sandboxed step gracefully when the dev box doesn't have docker."""
        ...
