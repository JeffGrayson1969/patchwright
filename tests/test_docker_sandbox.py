"""DockerSandbox unit tests — subprocess fully mocked, no daemon required."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from patchwright.core.sandbox import Mount, SandboxError
from patchwright.sandboxes.docker import DockerSandbox

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


def _ok_run(stdout: str = "out", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Build a SimpleNamespace that mimics CompletedProcess for the run() call,
    while keeping is_available()'s precondition mock simple."""
    return MagicMock(
        side_effect=[
            SimpleNamespace(returncode=0),  # is_available() docker version
            SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr),  # actual run
        ]
    )


def test_run_returns_runresult(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run(stdout="hello\n")) as run,
    ):
        result = sb.run(image="alpine", cmd=["echo", "hello"])
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.image == "alpine"
    assert result.cmd == ("echo", "hello")
    assert result.network_enabled is False
    # The second call (after is_available) is the actual docker run
    actual_call_args = run.call_args_list[1].args[0]
    assert actual_call_args[1] == "run"
    assert "--rm" in actual_call_args


def test_run_default_network_is_none(tmp_path: Path) -> None:
    """NFR-S-2: network default-deny. Verify --network=none is in the argv."""
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()) as run,
    ):
        sb.run(image="alpine", cmd=["true"])
    docker_args = run.call_args_list[1].args[0]
    assert "--network=none" in docker_args


def test_run_with_network_omits_network_none(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()) as run,
    ):
        result = sb.run(image="alpine", cmd=["true"], network=True)
    docker_args = run.call_args_list[1].args[0]
    assert "--network=none" not in docker_args
    assert result.network_enabled is True


def test_run_with_mounts(tmp_path: Path) -> None:
    src = tmp_path / "data"
    src.mkdir()
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()) as run,
    ):
        sb.run(
            image="alpine",
            cmd=["ls", "/work"],
            mounts=[Mount(source=src, target="/work")],
        )
    docker_args = run.call_args_list[1].args[0]
    assert "-v" in docker_args
    v_value = docker_args[docker_args.index("-v") + 1]
    assert f"{src}:/work:ro" == v_value


def test_run_with_writable_mount(tmp_path: Path) -> None:
    src = tmp_path / "scratch"
    src.mkdir()
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()) as run,
    ):
        sb.run(
            image="alpine",
            cmd=["touch", "/scratch/x"],
            mounts=[Mount(source=src, target="/scratch", readonly=False)],
        )
    docker_args = run.call_args_list[1].args[0]
    v_value = docker_args[docker_args.index("-v") + 1]
    assert f"{src}:/scratch" == v_value  # no :ro suffix


def test_run_with_env(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()) as run,
    ):
        result = sb.run(image="alpine", cmd=["env"], env={"FOO": "bar", "BAZ": "1"})
    docker_args = run.call_args_list[1].args[0]
    assert "-e" in docker_args
    e_indices = [i for i, a in enumerate(docker_args) if a == "-e"]
    e_values = [docker_args[i + 1] for i in e_indices]
    assert "FOO=bar" in e_values
    assert "BAZ=1" in e_values
    assert result.env == {"FOO": "bar", "BAZ": "1"}


# --------------------------------------------------------------------------- run — failure


def test_run_nonzero_exit_returns_runresult(tmp_path: Path) -> None:
    """Non-zero exit is a normal outcome, not an exception."""
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run(stderr="bad", returncode=42)),
    ):
        result = sb.run(image="alpine", cmd=["false"])
    assert result.exit_code == 42
    assert result.stderr == "bad"
    assert result.timed_out is False


def test_run_timeout_returns_timed_out_result(tmp_path: Path) -> None:
    sb = DockerSandbox()

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        # First call is is_available()'s docker version, second is the actual run
        if "version" in args[0]:
            return SimpleNamespace(returncode=0)
        raise subprocess.TimeoutExpired(cmd="docker run", timeout=1.0, stderr=b"slow")

    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = sb.run(image="alpine", cmd=["sleep", "9999"], timeout=1.0)
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stderr == "slow"


def test_run_raises_when_docker_unavailable(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(SandboxError, match="not available"),
    ):
        sb.run(image="alpine", cmd=["true"])


# --------------------------------------------------------------------------- validation


def test_mount_with_nonabsolute_source_rejected() -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()),
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
        patch("subprocess.run", new=_ok_run()),
        pytest.raises(SandboxError, match="does not exist"),
    ):
        sb.run(
            image="alpine",
            cmd=["true"],
            mounts=[Mount(source=tmp_path / "nope", target="/work")],
        )


def test_env_key_with_equals_rejected(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()),
        pytest.raises(SandboxError, match="invalid env var key"),
    ):
        sb.run(image="alpine", cmd=["true"], env={"BAD=KEY": "v"})


def test_env_key_with_whitespace_rejected(tmp_path: Path) -> None:
    sb = DockerSandbox()
    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run", new=_ok_run()),
        pytest.raises(SandboxError, match="invalid env var key"),
    ):
        sb.run(image="alpine", cmd=["true"], env={"BAD KEY": "v"})
