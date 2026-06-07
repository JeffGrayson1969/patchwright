"""DockerSandbox unit tests — subprocess fully mocked, no daemon required."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from patchwright.core.sandbox import Mount, NetworkPolicy, ResourceLimits, SandboxError
from patchwright.sandboxes.docker import DockerSandbox

# --------------------------------------------------------------------------- helpers


def _make_popen_mock(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    """Build a mock Popen object with readable stdout/stderr streams."""
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.stdout = io.BytesIO(stdout)
    mock.stderr = io.BytesIO(stderr)
    mock.returncode = returncode
    mock.wait = MagicMock(return_value=returncode)
    mock.kill = MagicMock()
    return mock


def _ok_subprocess_run(returncode: int = 0) -> MagicMock:
    """Stub for the is_available() `subprocess.run` call."""
    return MagicMock(return_value=SimpleNamespace(returncode=returncode))


# --------------------------------------------------------------------------- is_available


def test_is_available_false_when_binary_missing() -> None:
    sb = DockerSandbox()
    with patch("shutil.which", return_value=None):
        assert sb.is_available() is False


def test_is_available_true_when_version_succeeds() -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        assert sb.is_available() is True


def test_is_available_false_when_version_fails() -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = SimpleNamespace(returncode=1)
        assert sb.is_available() is False


def test_is_available_caches() -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker") as which,
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0)
        sb.is_available()
        sb.is_available()
        sb.is_available()
        assert which.call_count == 1


def test_is_available_handles_timeout() -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=10)),
    ):
        assert sb.is_available() is False


# --------------------------------------------------------------------------- run — happy path


def test_run_returns_runresult(tmp_path: Path) -> None:
    sb = DockerSandbox()
    popen_mock = _make_popen_mock(stdout=b"hello\n")
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        result = sb.run(image="alpine", cmd=["echo", "hello"])
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.image == "alpine"
    assert result.cmd == ("echo", "hello")
    assert result.network_enabled is False
    assert result.truncated is False
    call_args = popen.call_args[0][0]
    assert call_args[1] == "run"
    assert "--rm" in call_args


def test_run_default_network_is_none(tmp_path: Path) -> None:
    """NFR-S-2: network default-deny. Verify --network=none is in the argv."""
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        sb.run(image="alpine", cmd=["true"])
    docker_args = popen.call_args[0][0]
    assert "--network=none" in docker_args


def test_run_with_network_policy_bridge(tmp_path: Path) -> None:
    """bridge mode omits --network=none."""
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        result = sb.run(
            image="alpine",
            cmd=["true"],
            network_policy=NetworkPolicy(mode="bridge"),
        )
    docker_args = popen.call_args[0][0]
    assert "--network=none" not in docker_args
    assert result.network_enabled is True


def test_run_with_network_policy_allowlist_falls_back_to_none(tmp_path: Path) -> None:
    """allowlist falls back to --network=none until M3-hard implements it."""
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        result = sb.run(
            image="alpine",
            cmd=["true"],
            network_policy=NetworkPolicy(mode="allowlist", allowlist=["8.8.8.8/32"]),
        )
    docker_args = popen.call_args[0][0]
    assert "--network=none" in docker_args
    # allowlist mode does not set network_enabled (bridge is the only enabled mode)
    assert result.network_enabled is False


def test_run_with_mounts(tmp_path: Path) -> None:
    src = tmp_path / "data"
    src.mkdir()
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        sb.run(
            image="alpine",
            cmd=["ls", "/work"],
            mounts=[Mount(source=src, target="/work")],
        )
    docker_args = popen.call_args[0][0]
    assert "-v" in docker_args
    v_value = docker_args[docker_args.index("-v") + 1]
    # resolve() on a real dir should equal itself (no symlinks in tmp_path normally)
    assert v_value.endswith(":/work:ro")


def test_run_with_writable_mount(tmp_path: Path) -> None:
    src = tmp_path / "scratch"
    src.mkdir()
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        sb.run(
            image="alpine",
            cmd=["touch", "/scratch/x"],
            mounts=[Mount(source=src, target="/scratch", readonly=False)],
        )
    docker_args = popen.call_args[0][0]
    v_value = docker_args[docker_args.index("-v") + 1]
    assert v_value.endswith(":/scratch")  # no :ro suffix


def test_run_with_env(tmp_path: Path) -> None:
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        result = sb.run(image="alpine", cmd=["env"], env={"FOO": "bar", "BAZ": "1"})
    docker_args = popen.call_args[0][0]
    assert "-e" in docker_args
    e_indices = [i for i, a in enumerate(docker_args) if a == "-e"]
    e_values = [docker_args[i + 1] for i in e_indices]
    assert "FOO=bar" in e_values
    assert "BAZ=1" in e_values
    assert result.env == {"FOO": "bar", "BAZ": "1"}


# --------------------------------------------------------------------------- run — resource limits


def test_run_default_memory_and_pids_in_args() -> None:
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        sb.run(image="alpine", cmd=["true"])
    docker_args = popen.call_args[0][0]
    assert "--memory=256m" in docker_args
    assert "--pids-limit=64" in docker_args


def test_run_resource_limits_override_defaults() -> None:
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        sb.run(
            image="alpine",
            cmd=["true"],
            resource_limits=ResourceLimits(memory="512m", pids_limit=128),
        )
    docker_args = popen.call_args[0][0]
    assert "--memory=512m" in docker_args
    assert "--pids-limit=128" in docker_args
    assert "--memory=256m" not in docker_args


# --------------------------------------------------------------------------- run — nobody user


def test_run_adds_user_nobody() -> None:
    """--user=nobody must be present (dev backend, no userns-remap)."""
    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        sb.run(image="alpine", cmd=["true"])
    docker_args = popen.call_args[0][0]
    assert "--user=nobody" in docker_args


# --------------------------------------------------------------------------- run — failure


def test_run_nonzero_exit_returns_runresult(tmp_path: Path) -> None:
    """Non-zero exit is a normal outcome, not an exception."""
    sb = DockerSandbox()
    popen_mock = _make_popen_mock(stderr=b"bad", returncode=42)
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock),
    ):
        result = sb.run(image="alpine", cmd=["false"])
    assert result.exit_code == 42
    assert result.stderr == "bad"
    assert result.timed_out is False


def test_run_timeout_returns_timed_out_result(tmp_path: Path) -> None:
    sb = DockerSandbox()
    popen_mock = _make_popen_mock(stderr=b"slow")
    popen_mock.wait = MagicMock(
        side_effect=subprocess.TimeoutExpired(cmd="docker run", timeout=1.0)
    )
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock),
        patch("patchwright.sandboxes.docker._kill_container"),
    ):
        result = sb.run(image="alpine", cmd=["sleep", "9999"], timeout=1.0)
    assert result.timed_out is True
    assert result.exit_code == -1


def test_run_raises_when_docker_unavailable(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(SandboxError, match="not available"),
    ):
        sb.run(image="alpine", cmd=["true"])


# --------------------------------------------------------------------------- run — truncation


def test_run_truncation_flag_set_when_output_exceeds_cap() -> None:
    """truncated=True when stdout hits max_output_bytes."""
    cap = 16
    sb = DockerSandbox(max_output_bytes=cap)
    # Produce more bytes than the cap
    big_output = b"x" * (cap + 1)
    popen_mock = _make_popen_mock(stdout=big_output)
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock),
    ):
        result = sb.run(image="alpine", cmd=["true"])
    assert result.truncated is True
    assert len(result.stdout.encode()) <= cap


def test_run_no_truncation_flag_when_output_under_cap() -> None:
    cap = 1_048_576
    sb = DockerSandbox(max_output_bytes=cap)
    popen_mock = _make_popen_mock(stdout=b"small output")
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock),
    ):
        result = sb.run(image="alpine", cmd=["true"])
    assert result.truncated is False


# --------------------------------------------------------------------------- validation


def test_mount_with_nonabsolute_source_rejected() -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        pytest.raises(SandboxError, match="must be absolute"),
    ):
        sb.run(
            image="alpine",
            cmd=["true"],
            mounts=[Mount(source=Path("relative/path"), target="/work")],
        )


def test_mount_with_missing_source_rejected(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        pytest.raises(SandboxError, match="does not exist"),
    ):
        sb.run(
            image="alpine",
            cmd=["true"],
            mounts=[Mount(source=tmp_path / "nope", target="/work")],
        )


def test_mount_symlink_outside_dir_is_canonicalized(tmp_path: Path) -> None:
    """resolve(strict=True) follows symlinks; the resolved real path is used in docker -v."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_dir)

    sb = DockerSandbox()
    popen_mock = _make_popen_mock()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        sb.run(
            image="alpine",
            cmd=["ls", "/work"],
            mounts=[Mount(source=link, target="/work")],
        )
    docker_args = popen.call_args[0][0]
    v_value = docker_args[docker_args.index("-v") + 1]
    # The -v arg must use the resolved real path, not the symlink path.
    assert str(real_dir.resolve()) in v_value


def test_mount_dangling_symlink_rejected(tmp_path: Path) -> None:
    """A symlink pointing to a non-existent target must be rejected."""
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nonexistent_target")

    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        pytest.raises(SandboxError, match="does not exist"),
    ):
        sb.run(
            image="alpine",
            cmd=["true"],
            mounts=[Mount(source=dangling, target="/work")],
        )


def test_env_key_with_equals_rejected(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        pytest.raises(SandboxError, match="invalid env var key"),
    ):
        sb.run(image="alpine", cmd=["true"], env={"BAD=KEY": "v"})


def test_env_key_with_whitespace_rejected(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        pytest.raises(SandboxError, match="invalid env var key"),
    ):
        sb.run(image="alpine", cmd=["true"], env={"BAD KEY": "v"})
