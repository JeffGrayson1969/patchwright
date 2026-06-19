"""GVisorSandbox unit tests — subprocess fully mocked, no daemon or runsc required.

The hard negative tests (real network-deny, real RO FS escape) live in M3-hard.3
(AEG-463) where they ride a real PoC fixture and are gated by runsc availability.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from patchwright.core.sandbox import (
    Mount,
    NetworkPolicy,
    ResourceLimits,
    SandboxError,
    SandboxRunner,
)
from patchwright.sandboxes.gvisor import GVisorSandbox

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


_DEFAULT_INFO_STDOUT = b'{"runsc": {"path": "/usr/bin/runsc"}}'


def _which_for(binaries: dict[str, str | None]) -> Any:
    """Return a side_effect function that maps binary name -> path|None."""

    def _lookup(b: str) -> str | None:
        return binaries.get(b)

    return _lookup


def _available_subprocess_run(
    runsc_rc: int = 0,
    info_rc: int = 0,
    info_stdout: bytes = _DEFAULT_INFO_STDOUT,
) -> Any:
    """Return a side_effect for subprocess.run that distinguishes runsc vs docker calls."""

    def _side_effect(args: list[str], **_: Any) -> SimpleNamespace:
        if args and args[0].endswith("runsc"):
            return SimpleNamespace(returncode=runsc_rc, stdout=b"runsc version", stderr=b"")
        if args and args[0].endswith("docker"):
            return SimpleNamespace(returncode=info_rc, stdout=info_stdout, stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    return _side_effect


def _patched_available(
    runsc_rc: int = 0,
    info_rc: int = 0,
    info_stdout: bytes = _DEFAULT_INFO_STDOUT,
) -> tuple[Any, Any]:
    return (
        patch(
            "shutil.which",
            side_effect=_which_for({"runsc": "/usr/bin/runsc", "docker": "/usr/bin/docker"}),
        ),
        patch(
            "subprocess.run",
            side_effect=_available_subprocess_run(runsc_rc, info_rc, info_stdout),
        ),
    )


# --------------------------------------------------------------------------- Protocol conformance


def test_satisfies_sandbox_runner_protocol() -> None:
    assert isinstance(GVisorSandbox(), SandboxRunner)


def test_default_name_is_gvisor() -> None:
    assert GVisorSandbox().name == "gvisor"


# --------------------------------------------------------------------------- is_available


def test_is_available_false_when_runsc_missing() -> None:
    sb = GVisorSandbox()
    binaries = {"runsc": None, "docker": "/usr/bin/docker"}
    with patch("shutil.which", side_effect=_which_for(binaries)):
        assert sb.is_available() is False


def test_is_available_false_when_docker_missing() -> None:
    sb = GVisorSandbox()
    with patch("shutil.which", side_effect=_which_for({"runsc": "/usr/bin/runsc", "docker": None})):
        assert sb.is_available() is False


def test_is_available_false_when_runsc_version_fails() -> None:
    sb = GVisorSandbox()
    which, run = _patched_available(runsc_rc=1)
    with which, run:
        assert sb.is_available() is False


def test_is_available_false_when_runtime_not_registered() -> None:
    """runsc binary present but not registered as a Docker runtime → False."""
    sb = GVisorSandbox()
    which, run = _patched_available(info_stdout=b'{"runc": {"path": "/usr/bin/runc"}}')
    with which, run:
        assert sb.is_available() is False


def test_is_available_true_when_all_checks_pass() -> None:
    sb = GVisorSandbox()
    which, run = _patched_available()
    with which, run:
        assert sb.is_available() is True


def test_is_available_caches() -> None:
    sb = GVisorSandbox()
    which_patcher, run_patcher = _patched_available()
    with which_patcher as which_mock, run_patcher as run_mock:
        sb.is_available()
        sb.is_available()
        sb.is_available()
        # Each call needed two `shutil.which` (runsc + docker) and two
        # `subprocess.run` calls — only the first invocation must do the work.
        assert which_mock.call_count == 2
        assert run_mock.call_count == 2


def test_is_available_handles_timeout() -> None:
    sb = GVisorSandbox()
    with (
        patch(
            "shutil.which",
            side_effect=_which_for({"runsc": "/usr/bin/runsc", "docker": "/usr/bin/docker"}),
        ),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="runsc", timeout=10)),
    ):
        assert sb.is_available() is False


def test_is_available_handles_malformed_runtimes_json() -> None:
    sb = GVisorSandbox()
    which, run = _patched_available(info_stdout=b"not-json")
    with which, run:
        assert sb.is_available() is False


# --------------------------------------------------------------------------- run — defaults


def _run_with_default_mocks(sb: GVisorSandbox, **kwargs: Any) -> tuple[Any, list[str]]:
    """Invoke run() under standard 'sandbox is available' mocks and return (result, argv)."""
    popen_mock = _make_popen_mock()
    which, sub_run = _patched_available()
    with (
        which,
        sub_run,
        patch("subprocess.Popen", return_value=popen_mock) as popen,
    ):
        result = sb.run(image="alpine", cmd=["true"], **kwargs)
    return result, popen.call_args[0][0]


def test_run_default_uses_runsc_runtime() -> None:
    """NFR-S-1: container runtime is gVisor (`--runtime=runsc`)."""
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert "--runtime=runsc" in args


def test_run_default_rootfs_is_read_only() -> None:
    """NFR-S-3: container rootfs is RO by default."""
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert "--read-only" in args


def test_run_default_mounts_writable_tmpfs_at_tmp() -> None:
    """--read-only rootfs needs a small tmpfs at /tmp for most workloads."""
    _, args = _run_with_default_mocks(GVisorSandbox())
    tmpfs_args = [a for a in args if a.startswith("--tmpfs=/tmp")]
    assert len(tmpfs_args) == 1
    assert "noexec" in tmpfs_args[0]
    assert "nosuid" in tmpfs_args[0]
    assert "size=" in tmpfs_args[0]


def test_run_default_drops_all_caps_and_no_new_privs() -> None:
    """T6 defense in depth — even if a workload tries to escalate, can't."""
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert "--security-opt=no-new-privileges" in args
    assert "--cap-drop=ALL" in args


def test_run_default_network_is_none() -> None:
    """NFR-S-2: network default-deny."""
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert "--network=none" in args


def test_run_with_network_policy_bridge_omits_none() -> None:
    """Explicit bridge mode opts into Docker's default bridge."""
    sb = GVisorSandbox()
    result, args = _run_with_default_mocks(sb, network_policy=NetworkPolicy(mode="bridge"))
    assert "--network=none" not in args
    assert result.network_enabled is True


def test_run_with_allowlist_falls_back_to_deny_for_now() -> None:
    """allowlist falls back to --network=none until M3-hard.3 implements per-case rules."""
    sb = GVisorSandbox()
    result, args = _run_with_default_mocks(
        sb, network_policy=NetworkPolicy(mode="allowlist", allowlist=["8.8.8.8/32"])
    )
    assert "--network=none" in args
    assert result.network_enabled is False


def test_run_default_user_nobody() -> None:
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert "--user=nobody" in args


# --------------------------------------------------------------------------- run — resource limits


def test_run_default_memory_and_pids() -> None:
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert "--memory=256m" in args
    assert "--pids-limit=64" in args


def test_run_default_no_cpus_arg_when_unset() -> None:
    """cpus omitted from argv when neither the sandbox nor the call sets it."""
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert not any(a.startswith("--cpus=") for a in args)


def test_run_cpus_default_applied_when_sandbox_configures_it() -> None:
    sb = GVisorSandbox(cpus=0.5)
    _, args = _run_with_default_mocks(sb)
    assert "--cpus=0.5" in args


def test_run_resource_limits_override_defaults() -> None:
    sb = GVisorSandbox()
    _, args = _run_with_default_mocks(
        sb, resource_limits=ResourceLimits(memory="512m", pids_limit=128, cpus=1.5)
    )
    assert "--memory=512m" in args
    assert "--pids-limit=128" in args
    assert "--cpus=1.5" in args
    assert "--memory=256m" not in args


# --------------------------------------------------------------------------- run — mounts


def test_run_default_mount_is_readonly(tmp_path: Path) -> None:
    src = tmp_path / "data"
    src.mkdir()
    sb = GVisorSandbox()
    _, args = _run_with_default_mocks(sb, mounts=[Mount(source=src, target="/work")])
    v_value = args[args.index("-v") + 1]
    assert v_value.endswith(":/work:ro")


def test_run_writable_mount_when_explicitly_opted_in(tmp_path: Path) -> None:
    src = tmp_path / "scratch"
    src.mkdir()
    sb = GVisorSandbox()
    _, args = _run_with_default_mocks(
        sb, mounts=[Mount(source=src, target="/scratch", readonly=False)]
    )
    v_value = args[args.index("-v") + 1]
    assert v_value.endswith(":/scratch")
    assert not v_value.endswith(":ro")


# --------------------------------------------------------------------------- run — env


def test_run_env_passed_through() -> None:
    sb = GVisorSandbox()
    result, args = _run_with_default_mocks(sb, env={"FOO": "bar"})
    e_indices = [i for i, a in enumerate(args) if a == "-e"]
    e_values = [args[i + 1] for i in e_indices]
    assert "FOO=bar" in e_values
    assert result.env == {"FOO": "bar"}


# --------------------------------------------------------------------------- run — availability


def test_run_raises_when_gvisor_unavailable() -> None:
    sb = GVisorSandbox()
    with (
        patch("shutil.which", side_effect=_which_for({"runsc": None, "docker": None})),
        pytest.raises(SandboxError, match="gvisor backend not available"),
    ):
        sb.run(image="alpine", cmd=["true"])


# --------------------------------------------------------------------------- run — timeout
# (delegated to the shared _run_popen helper; we only need to verify the integration.)


def test_run_timeout_returns_timed_out_result() -> None:
    sb = GVisorSandbox()
    popen_mock = _make_popen_mock(stderr=b"slow")
    popen_mock.wait = MagicMock(
        side_effect=subprocess.TimeoutExpired(cmd="docker run", timeout=1.0)
    )
    which, sub_run = _patched_available()
    with (
        which,
        sub_run,
        patch("subprocess.Popen", return_value=popen_mock),
        patch("patchwright.sandboxes.docker._kill_container"),
    ):
        result = sb.run(image="alpine", cmd=["sleep", "9999"], timeout=1.0)
    assert result.timed_out is True
    assert result.exit_code == -1


def test_run_truncation_flag_set_when_output_exceeds_cap() -> None:
    cap = 16
    sb = GVisorSandbox(max_output_bytes=cap)
    popen_mock = _make_popen_mock(stdout=b"x" * (cap + 1))
    which, sub_run = _patched_available()
    with which, sub_run, patch("subprocess.Popen", return_value=popen_mock):
        result = sb.run(image="alpine", cmd=["true"])
    assert result.truncated is True
    assert len(result.stdout.encode()) <= cap


# --------------------------------------------------------------------------- run — argv ordering


def test_argv_image_and_cmd_come_after_double_dash() -> None:
    """`--` must separate docker's args from the workload image/cmd so a
    workload like `--privileged` in cmd can't pose as a docker flag."""
    _, args = _run_with_default_mocks(GVisorSandbox())
    assert "--" in args
    dash_idx = args.index("--")
    assert args[dash_idx + 1] == "alpine"
    assert args[dash_idx + 2] == "true"


# --------------------------------------------------------------------------- validation


def test_mount_with_nonabsolute_source_rejected(tmp_path: Path) -> None:
    sb = GVisorSandbox()
    which, sub_run = _patched_available()
    with which, sub_run, pytest.raises(SandboxError, match="must be absolute"):
        sb.run(
            image="alpine",
            cmd=["true"],
            mounts=[Mount(source=Path("relative/path"), target="/work")],
        )


def test_env_key_with_equals_rejected() -> None:
    sb = GVisorSandbox()
    which, sub_run = _patched_available()
    with which, sub_run, pytest.raises(SandboxError, match="invalid env var key"):
        sb.run(image="alpine", cmd=["true"], env={"BAD=KEY": "v"})
