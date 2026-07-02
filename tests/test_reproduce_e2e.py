"""End-to-end: a real-CVE PoC drives TRIAGED -> REPRODUCED through drive() (AEG-463).

Uses a stub sandbox so this always runs (no docker/gVisor needed) and exercises
the full orchestrator path: open_case -> stub triage (INTAKE->TRIAGED) ->
ReproduceAgent (TRIAGED->REPRODUCED) -> pause at REPRODUCED (no agent for it).
Proves the repro_log artifact lands in the journal, which is the AEG-375 exit
criterion for the wiring. The real-sandbox reproduction rides
test_reproduce_cve_integration.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from patchwright.agents.reproduce import ReproduceAgent
from patchwright.core.artifacts import ArtifactStore, ReadOnlyArtifactStore
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.journal import Journal
from patchwright.core.models import AgentResult, Case, Transition
from patchwright.core.orchestrator import case_root_paths, drive, open_case
from patchwright.core.registry import Registry
from patchwright.core.sandbox import Mount, NetworkPolicy, ResourceLimits, RunResult
from patchwright.models.repro import ReproLog
from tests.repro_fixtures import cve_2007_4559_poc_spec


@dataclass
class _StubTriage:
    """Minimal INTAKE -> TRIAGED agent so drive() can reach the reproduce edge."""

    name: str = "stub_triage"
    handles_state: str = field(default=str(State.INTAKE))

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.INTAKE),
                to_state=str(State.TRIAGED),
                reason="stub",
            ),
            reason="stub",
        )


@dataclass
class _StubSandbox:
    """Returns a canned RunResult; simulates a PoC that reproduces (exit 0)."""

    name: str = "stub"

    def is_available(self) -> bool:
        return True

    def run(
        self,
        *,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        network_policy: NetworkPolicy | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> RunResult:
        return RunResult(
            exit_code=0,
            stdout="VULNERABLE: member escaped the extraction directory (CVE-2007-4559)\n",
            stderr="",
            timed_out=False,
            image=image,
            cmd=tuple(cmd),
        )


def test_cve_poc_drives_triaged_to_reproduced_e2e(tmp_path: Path) -> None:
    root = tmp_path / "cases"
    spec = cve_2007_4559_poc_spec()
    spec_bytes = canonical_json(spec.model_dump(mode="json"))

    case = open_case(
        case_id="case-cve20074559",
        root=root,
        raw_report=b'{"summary": "tarfile traversal"}',
        extra_artifacts=[(spec_bytes, "poc_spec", "application/json")],
    )
    assert case.state == str(State.INTAKE)

    registry = Registry()
    registry.register(_StubTriage())
    registry.register(ReproduceAgent(sandbox=_StubSandbox(), case_root=root / "scratch_root"))

    final = drive(case.id, registry, root)

    # REPRODUCED is non-terminal and has no agent here, so drive() pauses on it.
    assert final.state == str(State.REPRODUCED)

    # The repro_log artifact is present on the case and validates.
    repro_logs = [a for a in final.artifacts if a.kind == "repro_log"]
    assert len(repro_logs) == 1

    paths = case_root_paths(root, case.id)
    store = ArtifactStore(paths["artifacts_dir"]).read_only()
    log = ReproLog.model_validate_json(store.get(repro_logs[0].id))
    assert log.verdict == "reproduced"
    assert log.exit_code == 0
    assert log.case_id == case.id
    assert log.cmd == ("sh", "/poc/poc.sh")

    # The transition sequence is journaled: opened -> TRIAGED -> REPRODUCED.
    entries = Journal(paths["journal_dir"]).read()
    transitions = [e.payload["to_state"] for e in entries if e.kind == "transition"]
    assert transitions == [str(State.TRIAGED), str(State.REPRODUCED)]
