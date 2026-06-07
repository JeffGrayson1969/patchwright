"""SandboxRunner Protocol + Mount/RunResult shape."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from patchwright.core.sandbox import (
    Mount,
    NetworkPolicy,
    ResourceLimits,
    RunResult,
    SandboxError,
    SandboxRunner,
)
from patchwright.sandboxes.docker import DockerSandbox


def test_docker_satisfies_protocol() -> None:
    assert isinstance(DockerSandbox(), SandboxRunner)


def test_mount_defaults_readonly(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    m = Mount(source=src, target="/work")
    assert m.readonly is True


def test_mount_is_frozen(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    m = Mount(source=src, target="/work")
    with pytest.raises(AttributeError):
        m.target = "/other"  # type: ignore[misc]


def test_runresult_defaults() -> None:
    r = RunResult(
        exit_code=0,
        stdout="",
        stderr="",
        timed_out=False,
        image="alpine",
        cmd=("echo", "hi"),
    )
    assert r.network_enabled is False  # default-deny per NFR-S-2
    assert r.env == {}
    assert r.truncated is False


def test_runresult_is_frozen() -> None:
    r = RunResult(
        exit_code=0,
        stdout="",
        stderr="",
        timed_out=False,
        image="alpine",
        cmd=("echo", "hi"),
    )
    with pytest.raises(ValidationError):
        r.exit_code = 1  # type: ignore[misc]


def test_sandbox_error_is_exception() -> None:
    assert issubclass(SandboxError, Exception)


def test_resource_limits_defaults() -> None:
    rl = ResourceLimits()
    assert rl.memory is None
    assert rl.pids_limit is None
    assert rl.cpus is None


def test_resource_limits_is_frozen() -> None:
    rl = ResourceLimits(memory="512m")
    with pytest.raises(ValidationError):
        rl.memory = "1g"  # type: ignore[misc]


def test_network_policy_defaults_to_none_mode() -> None:
    np = NetworkPolicy()
    assert np.mode == "none"
    assert np.allowlist == []


def test_network_policy_bridge_mode() -> None:
    np = NetworkPolicy(mode="bridge")
    assert np.mode == "bridge"


def test_network_policy_allowlist_mode() -> None:
    np = NetworkPolicy(mode="allowlist", allowlist=["8.8.8.8/32"])
    assert np.mode == "allowlist"
    assert "8.8.8.8/32" in np.allowlist
