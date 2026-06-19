"""GVisorSandbox real-runtime smoke tests.

Skipped wherever the host can't run gVisor (no `runsc` binary or no
`runsc` runtime registered with Docker). On macOS dev this always
skips; on Linux CI with gVisor installed these exercise the real
hardened backend.

The hard T6 regression suite — network egress denied + RO FS escape
blocked under real workloads — rides the e2e fixture in M3-hard.3
(AEG-463) so it shares the same PoC corpus the reproduce agent uses.
This file only covers the basic "the backend can run something" exit
criterion for M3-hard.1.
"""

from __future__ import annotations

import pytest

from patchwright.sandboxes.gvisor import GVisorSandbox

pytestmark = pytest.mark.gvisor


@pytest.fixture(scope="module")
def gvisor_sandbox() -> GVisorSandbox:
    sb = GVisorSandbox()
    if not sb.is_available():
        pytest.skip("gvisor runtime (runsc) not available")
    return sb


def test_echo_in_alpine_under_gvisor(gvisor_sandbox: GVisorSandbox) -> None:
    """Smoke: a trivial workload runs under runsc and stdout comes back."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["echo", "patchwright-gvisor-ok"],
        timeout=60.0,
    )
    assert result.exit_code == 0
    assert "patchwright-gvisor-ok" in result.stdout
    assert result.timed_out is False
    assert result.network_enabled is False


def test_readonly_rootfs_blocks_write_to_etc(gvisor_sandbox: GVisorSandbox) -> None:
    """NFR-S-3 smoke: the rootfs is RO so the workload can't poison /etc/passwd."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "echo malicious > /etc/passwd 2>&1; echo EXIT=$?"],
        timeout=30.0,
    )
    # We don't assert the exact exit code (it's the inner `echo EXIT=$?`),
    # but the write must have failed — the shell prints the EXIT line of
    # the failed redirect.
    combined = result.stdout + result.stderr
    assert "EXIT=" in combined
    assert "EXIT=0" not in combined


def test_tmpfs_writable_at_tmp(gvisor_sandbox: GVisorSandbox) -> None:
    """Counterpart to the RO test: /tmp is mounted writable so workloads
    that need scratch space inside the rootfs still function."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "echo ok > /tmp/x && cat /tmp/x"],
        timeout=30.0,
    )
    assert result.exit_code == 0
    assert "ok" in result.stdout


def test_default_network_blocks_egress(gvisor_sandbox: GVisorSandbox) -> None:
    """NFR-S-2 smoke: default NetworkPolicy(mode='none') prevents egress.
    The exhaustive negative tests live in M3-hard.3."""
    result = gvisor_sandbox.run(
        image="alpine:3.20",
        cmd=["sh", "-c", "wget -q --timeout=5 -O- https://example.com 2>&1; echo EXIT=$?"],
        timeout=30.0,
    )
    combined = result.stdout + result.stderr
    assert "EXIT=0" not in combined
    assert result.network_enabled is False
