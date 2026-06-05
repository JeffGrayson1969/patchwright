"""DockerSandbox — subprocess wrapper around `docker run`.

Why subprocess instead of the docker Python SDK:
  - One less hard dep (docker-py)
  - Trivial to mock in tests (patch subprocess.run)
  - The CLI is the universal Docker interface; ergonomics are fine for our use
  - No sync/async client confusion

Hardened backend (gVisor) lives in sandboxes/gvisor.py — M3-hard, Wave B.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from patchwright.core.sandbox import Mount, RunResult, SandboxError


@dataclass
class DockerSandbox:
    """Run commands inside Docker containers. Default --network=none.

    Per NFR-S-2 the dev backend already enforces network-deny by default; the
    Wave B hardened backend will add seccomp, no-new-privileges, read-only
    rootfs, and a strict per-case allowlist on top.
    """

    name: str = "docker"
    docker_binary: str = "docker"
    """Path to the docker CLI. Override for tests / non-standard installs."""

    _availability_cache: bool | None = field(default=None, init=False, repr=False)

    def is_available(self) -> bool:
        """True iff `docker version` succeeds. Cached after the first call."""
        if self._availability_cache is not None:
            return self._availability_cache

        if shutil.which(self.docker_binary) is None:
            self._availability_cache = False
            return False

        try:
            result = subprocess.run(
                [self.docker_binary, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._availability_cache = False
            return False

        self._availability_cache = result.returncode == 0
        return self._availability_cache

    def run(
        self,
        *,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        network: bool = False,
    ) -> RunResult:
        """Execute `cmd` inside `image`. Returns a RunResult — does not raise
        for non-zero exits or timeouts."""
        if not self.is_available():
            raise SandboxError(
                f"docker backend not available (binary={self.docker_binary!r} not found or daemon "
                "not running)"
            )

        args = [self.docker_binary, "run", "--rm"]

        # Default-deny network (NFR-S-2). Opt-in only.
        if not network:
            args += ["--network=none"]

        for k, v in (env or {}).items():
            _validate_env_key(k)
            args += ["-e", f"{k}={v}"]

        for m in mounts or []:
            _validate_mount(m)
            ro = ":ro" if m.readonly else ""
            args += ["-v", f"{m.source}:{m.target}{ro}"]

        args += ["--", image, *cmd]

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return RunResult(
                exit_code=-1,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                image=image,
                cmd=tuple(cmd),
                env=dict(env or {}),
                network_enabled=network,
            )

        return RunResult(
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            timed_out=False,
            image=image,
            cmd=tuple(cmd),
            env=dict(env or {}),
            network_enabled=network,
        )


def _validate_env_key(key: str) -> None:
    """Reject env keys with whitespace or '=' — those would break the
    `-e KEY=VALUE` shell-arg shape (subprocess won't shell-interpret, but
    docker rejects them with a confusing error)."""
    if not key or "=" in key or any(c.isspace() for c in key):
        raise SandboxError(f"invalid env var key: {key!r}")


def _validate_mount(m: Mount) -> None:
    if not m.source.is_absolute():
        raise SandboxError(f"mount.source must be absolute, got {m.source}")
    if not m.target.startswith("/"):
        raise SandboxError(f"mount.target must be absolute path inside container, got {m.target}")
    if not m.source.exists():
        raise SandboxError(f"mount.source does not exist on host: {m.source}")
