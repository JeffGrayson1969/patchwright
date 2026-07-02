"""Real-sandbox reproduction of CVE-2007-4559 through the reproduce agent (AEG-463).

Drives the actual PoC — Python `tarfile.extractall` directory traversal — inside
a real Docker (and, where available, gVisor) sandbox via ReproduceAgent, proving
the AEG-375 exit criterion end-to-end: a real CVE PoC lands a `repro_log`
artifact with verdict=reproduced, and it does so with network egress disabled.

Auto-skipped on hosts without docker / the runsc runtime (e.g. macOS dev).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from patchwright.agents.reproduce import ReproduceAgent
from patchwright.core.artifacts import ArtifactStore
from patchwright.core.fsm import State
from patchwright.models.repro import ReproLog
from patchwright.sandboxes.docker import DockerSandbox
from patchwright.sandboxes.gvisor import GVisorSandbox
from tests.repro_fixtures import cve_2007_4559_poc_spec, triaged_case_with_poc


def _run_agent(sandbox: object, tmp_path: Path) -> ReproLog:
    store = ArtifactStore(tmp_path / "artifacts")
    spec = cve_2007_4559_poc_spec()
    case = triaged_case_with_poc(case_id="case-cve20074559", store=store, spec=spec)

    agent = ReproduceAgent(sandbox=sandbox, case_root=tmp_path / "case_root")  # type: ignore[arg-type]
    result = agent(case, store.read_only())

    assert result.transition.from_state == str(State.TRIAGED)
    assert result.transition.to_state == str(State.REPRODUCED)

    log_bytes, kind = result.new_artifacts[0]
    assert kind == "repro_log"
    return ReproLog.model_validate_json(log_bytes)


@pytest.mark.docker
def test_cve_2007_4559_reproduces_under_docker(tmp_path: Path) -> None:
    sb = DockerSandbox()
    if not sb.is_available():
        pytest.skip("docker daemon not available")
    log = _run_agent(sb, tmp_path)
    assert log.verdict == "reproduced"
    assert log.exit_code == 0
    assert log.network_enabled is False
    assert "CVE-2007-4559" in log.stdout_tail


@pytest.mark.gvisor
def test_cve_2007_4559_reproduces_under_gvisor(tmp_path: Path) -> None:
    sb = GVisorSandbox()
    if not sb.is_available():
        pytest.skip("gvisor runtime (runsc) not available")
    log = _run_agent(sb, tmp_path)
    assert log.verdict == "reproduced"
    assert log.exit_code == 0
    assert log.network_enabled is False
    assert "CVE-2007-4559" in log.stdout_tail
