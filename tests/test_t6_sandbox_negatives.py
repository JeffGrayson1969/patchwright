"""T6 (sandbox escape) negative tests under the hardened gVisor backend (AEG-463).

These are the exhaustive counterparts to the smoke checks in
test_gvisor_sandbox_integration.py: they prove the isolation *denies* what it
must — network egress (NFR-S-2) and writes to read-only paths (NFR-S-3) — while
a paired positive control proves the sandbox isn't just failing everything.

gVisor-gated: auto-skipped on hosts without the runsc runtime (e.g. macOS dev);
they run on Linux CI with gVisor installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from patchwright.core.sandbox import Mount, NetworkPolicy
from patchwright.sandboxes.gvisor import GVisorSandbox

pytestmark = pytest.mark.gvisor


@pytest.fixture(scope="module")
def gvisor_sandbox() -> GVisorSandbox:
    sb = GVisorSandbox()
    if not sb.is_available():
        pytest.skip("gvisor runtime (runsc) not available")
    return sb


# --------------------------------------------------------------------------- network deny


def test_dns_resolution_denied(gvisor_sandbox: GVisorSandbox) -> None:
    """NFR-S-2: default NetworkPolicy(mode='none') gives no resolver."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "nslookup example.com 2>&1; echo EXIT=$?"],
        timeout=30.0,
    )
    combined = result.stdout + result.stderr
    assert "EXIT=0" not in combined
    assert result.network_enabled is False


def test_tcp_egress_denied(gvisor_sandbox: GVisorSandbox) -> None:
    """Direct-to-IP connect (no DNS) is also refused when network is denied."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        # 1.1.1.1:80 by IP so this probes egress, not name resolution.
        cmd=["sh", "-c", "wget -q --timeout=5 -O- http://1.1.1.1 2>&1; echo EXIT=$?"],
        timeout=30.0,
    )
    combined = result.stdout + result.stderr
    assert "EXIT=0" not in combined
    assert result.network_enabled is False


# --------------------------------------------------------------------------- read-only FS


def test_readonly_bind_mount_not_writable(gvisor_sandbox: GVisorSandbox, tmp_path: Path) -> None:
    """NFR-S-3: a Mount(readonly=True) — how the agent mounts the PoC at /poc —
    cannot be written by the workload, so a PoC can't tamper with its own inputs."""
    src = tmp_path / "poc"
    src.mkdir()
    (src / "poc.sh").write_text("echo hi\n", encoding="utf-8")

    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "echo tampered > /poc/poc.sh 2>&1; echo EXIT=$?"],
        mounts=[Mount(source=src, target="/poc", readonly=True)],
        timeout=30.0,
    )
    combined = result.stdout + result.stderr
    assert "EXIT=0" not in combined
    # Host-side file is untouched.
    assert (src / "poc.sh").read_text(encoding="utf-8") == "echo hi\n"


def test_writable_tmp_is_positive_control(gvisor_sandbox: GVisorSandbox) -> None:
    """Discriminating control: /tmp IS writable, so the deny tests above prove
    policy enforcement rather than a sandbox that fails every write."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "echo ok > /tmp/probe && cat /tmp/probe"],
        timeout=30.0,
    )
    assert result.exit_code == 0
    assert "ok" in result.stdout


@pytest.mark.skipif(
    os.environ.get("CI_NO_EGRESS") == "1",
    reason="egress not available in this environment",
)
def test_explicit_bridge_allows_egress_positive_control(
    gvisor_sandbox: GVisorSandbox,
) -> None:
    """Paired positive control for the egress-deny tests: when an operator
    explicitly opts into a bridge network, egress succeeds — proving the deny
    is the policy default, not an environmental failure."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "wget -q --timeout=10 -O- https://example.com"],
        timeout=30.0,
        network_policy=NetworkPolicy(mode="bridge"),
    )
    assert result.exit_code == 0
    assert result.network_enabled is True
