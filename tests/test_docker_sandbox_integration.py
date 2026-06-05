"""Real-docker integration test.

Marked with @pytest.mark.docker — auto-skipped when docker isn't available.
This is the M3-shim exit-criterion test: `SandboxRunner.run(...)` executes
a fixture PoC inside Docker and no network egress is allowed by default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from patchwright.core.sandbox import Mount
from patchwright.sandboxes.docker import DockerSandbox

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def docker_sandbox() -> DockerSandbox:
    sb = DockerSandbox()
    if not sb.is_available():
        pytest.skip("docker daemon not available")
    return sb


def test_echo_in_alpine(docker_sandbox: DockerSandbox) -> None:
    """The exit-criterion sanity check: run something inside a container,
    get back stdout."""
    result = docker_sandbox.run(
        image="alpine:3.20",
        cmd=["echo", "patchwright-sandbox-ok"],
        timeout=60.0,
    )
    assert result.exit_code == 0
    assert "patchwright-sandbox-ok" in result.stdout
    assert result.timed_out is False
    assert result.network_enabled is False


def test_default_network_blocks_egress(docker_sandbox: DockerSandbox) -> None:
    """NFR-S-2: without network=True, the container cannot reach the outside
    world. wget against an external host must fail."""
    result = docker_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "wget -q --timeout=5 -O- https://example.com"],
        timeout=30.0,
    )
    assert result.exit_code != 0  # wget should fail without network


def test_mount_passes_file_into_container(docker_sandbox: DockerSandbox, tmp_path: Path) -> None:
    src_dir = tmp_path / "data"
    src_dir.mkdir()
    (src_dir / "hello.txt").write_text("from-host\n", encoding="utf-8")

    result = docker_sandbox.run(
        image="alpine:3.20",
        cmd=["cat", "/mnt/hello.txt"],
        mounts=[Mount(source=src_dir, target="/mnt")],
        timeout=30.0,
    )
    assert result.exit_code == 0
    assert "from-host" in result.stdout


def test_env_var_visible_inside_container(docker_sandbox: DockerSandbox) -> None:
    result = docker_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "echo VAL=$PATCHWRIGHT_TEST"],
        env={"PATCHWRIGHT_TEST": "ok"},
        timeout=30.0,
    )
    assert result.exit_code == 0
    assert "VAL=ok" in result.stdout


def test_nonzero_exit_propagates(docker_sandbox: DockerSandbox) -> None:
    result = docker_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "exit 42"],
        timeout=30.0,
    )
    assert result.exit_code == 42
    assert result.timed_out is False
