"""DockerSandbox — subprocess wrapper around `docker run`.

Why subprocess instead of the docker Python SDK:
  - One less hard dep (docker-py)
  - Trivial to mock in tests (patch subprocess.run)
  - The CLI is the universal Docker interface; ergonomics are fine for our use
  - No sync/async client confusion

Hardened backend (gVisor) lives in sandboxes/gvisor.py — M3-hard, Wave B.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from patchwright.core.sandbox import (
    Mount,
    NetworkPolicy,
    ResourceLimits,
    RunResult,
    SandboxError,
)

_DEFAULT_MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB


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

    memory: str = "256m"
    pids_limit: int = 64
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES

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

    def _build_args(
        self,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None,
        env: dict[str, str] | None,
        policy: NetworkPolicy,
        eff_memory: str,
        eff_pids: int,
        cid_path: str,
    ) -> tuple[list[str], list[Path]]:
        """Build docker argv, returning (args, resolved_sources)."""
        args = [self.docker_binary, "run", "--rm"]

        if policy.mode in ("none", "allowlist"):
            if policy.mode == "allowlist":
                # TODO(M3-hard): implement per-case network allowlist via nftables/iptables.
                pass
            args += ["--network=none"]

        args += [f"--memory={eff_memory}", f"--pids-limit={eff_pids}"]

        # Runs as nobody; dev backend has no userns-remap so
        # root-in-container = root on mounted host paths.
        args += ["--user=nobody"]

        for k, v in (env or {}).items():
            _validate_env_key(k)
            args += ["-e", f"{k}={v}"]

        resolved_sources: list[Path] = []
        for m in mounts or []:
            resolved = _validate_mount(m)
            resolved_sources.append(resolved)
            ro = ":ro" if m.readonly else ""
            args += ["-v", f"{resolved}:{m.target}{ro}"]

        args += [f"--cidfile={cid_path}", "--", image, *cmd]
        return args, resolved_sources

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
    ) -> RunResult:
        """Execute `cmd` inside `image`. Returns a RunResult — does not raise
        for non-zero exits or timeouts."""
        if not self.is_available():
            raise SandboxError(
                f"docker backend not available (binary={self.docker_binary!r} not found or daemon "
                "not running)"
            )

        policy = network_policy or NetworkPolicy()
        limits = resource_limits or ResourceLimits()

        eff_memory = limits.memory if limits.memory is not None else self.memory
        eff_pids = limits.pids_limit if limits.pids_limit is not None else self.pids_limit

        # mkstemp creates the file; unlink it so --cidfile can write a fresh one.
        cid_fd, cid_path = tempfile.mkstemp(suffix=".cid")
        os.close(cid_fd)
        os.unlink(cid_path)

        args, _ = self._build_args(image, cmd, mounts, env, policy, eff_memory, eff_pids, cid_path)

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


def _run_popen(
    *,
    args: list[str],
    image: str,
    cmd: list[str],
    env: dict[str, str] | None,
    timeout: float,
    network_enabled: bool,
    max_output_bytes: int,
    docker_binary: str,
    cid_path: str,
) -> RunResult:
    """Spawn docker, capture bounded output, handle timeout."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stdout_buf: bytearray = bytearray()
    stderr_buf: bytearray = bytearray()

    t_out = threading.Thread(
        target=_read_stream, args=(proc.stdout, stdout_buf, max_output_bytes), daemon=True
    )
    t_err = threading.Thread(
        target=_read_stream, args=(proc.stderr, stderr_buf, max_output_bytes), daemon=True
    )
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_container(docker_binary, cid_path)
        proc.kill()
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        return RunResult(
            exit_code=-1,
            stdout=stdout_buf.decode("utf-8", errors="replace"),
            stderr=stderr_buf.decode("utf-8", errors="replace"),
            timed_out=True,
            truncated=len(stdout_buf) >= max_output_bytes or len(stderr_buf) >= max_output_bytes,
            image=image,
            cmd=tuple(cmd),
            env=dict(env or {}),
            network_enabled=network_enabled,
        )

    t_out.join(timeout=5)
    t_err.join(timeout=5)
    return RunResult(
        exit_code=proc.returncode,
        stdout=stdout_buf.decode("utf-8", errors="replace"),
        stderr=stderr_buf.decode("utf-8", errors="replace"),
        timed_out=False,
        truncated=len(stdout_buf) >= max_output_bytes or len(stderr_buf) >= max_output_bytes,
        image=image,
        cmd=tuple(cmd),
        env=dict(env or {}),
        network_enabled=network_enabled,
    )


def _read_stream(stream: object, buf: bytearray, cap: int) -> None:
    """Read from stream into buf up to cap bytes, then drain to unblock the process."""
    while True:
        remaining = cap - len(buf)
        if remaining <= 0:
            break
        chunk = stream.read(min(4096, remaining))  # type: ignore[union-attr]
        if not chunk:
            break
        buf += chunk
    with contextlib.suppress(Exception):
        stream.read()  # type: ignore[union-attr]


def _kill_container(docker_binary: str, cid_path: str) -> None:
    """Stop a container whose ID is in cid_path. Suppressed — must not mask TimeoutExpired."""
    with contextlib.suppress(Exception):
        cid = Path(cid_path).read_text().strip()
        if cid:
            subprocess.run(
                [docker_binary, "stop", "--time=0", cid],
                check=False,
                timeout=10,
                capture_output=True,
            )


def _validate_env_key(key: str) -> None:
    """Reject env keys with whitespace or '=' — those would break the
    `-e KEY=VALUE` shell-arg shape (subprocess won't shell-interpret, but
    docker rejects them with a confusing error)."""
    if not key or "=" in key or any(c.isspace() for c in key):
        raise SandboxError(f"invalid env var key: {key!r}")


def _validate_mount(m: Mount) -> Path:
    """Validate mount and return the resolved (canonical) source path."""
    if not m.source.is_absolute():
        raise SandboxError(f"mount.source must be absolute, got {m.source}")
    if not m.target.startswith("/"):
        raise SandboxError(f"mount.target must be absolute path inside container, got {m.target}")
    try:
        resolved = m.source.resolve(strict=True)
    except FileNotFoundError:
        raise SandboxError(f"mount.source does not exist on host: {m.source}") from None
    return resolved
