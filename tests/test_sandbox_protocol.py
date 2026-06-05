"""SandboxRunner Protocol + Mount/RunResult shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from patchwright.core.sandbox import Mount, RunResult, SandboxError, SandboxRunner
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


def test_sandbox_error_is_exception() -> None:
    assert issubclass(SandboxError, Exception)
