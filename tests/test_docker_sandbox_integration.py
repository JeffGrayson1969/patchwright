"""Real-docker integration test.

Marked with @pytest.mark.docker — auto-skipped when docker isn't available.
This is the M3-shim exit-criterion test: `SandboxRunner.run(...)` executes
a fixture PoC inside Docker and no network egress is allowed by default.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from patchwright.core.sandbox import Mount, NetworkPolicy
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
    """NFR-S-2: default NetworkPolicy(mode="none") blocks egress."""
    result = docker_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "wget -q --timeout=5 -O- https://example.com 2>&1; true"],
        timeout=30.0,
    )
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert any(s in combined.lower() for s in ("resolve", "unreachable", "bad address", "network"))


@pytest.mark.skipif(
    os.environ.get("CI_NO_EGRESS") == "1",
    reason="egress not available in this environment",
)
def test_bridge_network_allows_egress(docker_sandbox: DockerSandbox) -> None:
    """Positive control: NetworkPolicy(mode="bridge") permits egress.

    Paired with test_default_network_blocks_egress so the two tests together
    prove the policy discriminates rather than always passing or always failing.
    """
    result = docker_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "wget -q --timeout=10 -O- https://example.com"],
        timeout=30.0,
        network_policy=NetworkPolicy(mode="bridge"),
    )
    assert result.exit_code == 0
    assert result.network_enabled is True


def test_timeout_kills_container(docker_sandbox: DockerSandbox) -> None:
    """Regression: a timed-out run must not leave an orphan container running."""
    image = "alpine:3.20"

    # Snapshot running containers for this image before the run.
    before = set(
        subprocess.run(
            ["docker", "ps", "-q", "--filter", f"ancestor={image}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
    )

    result = docker_sandbox.run(
        image=image,
        cmd=["sleep", "9999"],
        timeout=2.0,
    )

    assert result.timed_out is True

    # docker stop --time=0 is async; poll briefly before asserting.
    # Snapshot is image-filtered so unrelated containers don't pollute the check.
    deadline = time.monotonic() + 5.0
    after: set[str] = set()
    while time.monotonic() < deadline:
        after = set(
            subprocess.run(
                ["docker", "ps", "-q", "--filter", f"ancestor={image}"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.split()
        )
        if after <= before:
            break
        time.sleep(0.5)

    assert after <= before, f"orphan containers still running: {after - before}"


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
