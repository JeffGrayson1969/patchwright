"""Real triage agent — mocked LLM, full end-to-end FSM walk.

Verifies:
  - the agent reads the raw_report artifact
  - the LLM is called with the system prompt + delimiter-wrapped report
  - dispositions map to the correct FSM target states
  - the TriagePacket is emitted as a journal artifact and verifiable
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from patchwright.agents.noop_closer import agent as noop_closer
from patchwright.agents.triage import (
    REPORT_DELIMITER,
    SYSTEM_PROMPT,
    TriageAgent,
    _load_raw_report,
)
from patchwright.cli.__main__ import main as cli_main
from patchwright.core.artifacts import ArtifactStore, ReadOnlyArtifactStore
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.journal import Journal
from patchwright.core.llm import LLMResponseError
from patchwright.core.models import Case
from patchwright.core.orchestrator import case_root_paths, drive, open_case
from patchwright.core.registry import Registry
from patchwright.models.triage import TriageDisposition, TriagePacket


@dataclass
class FakeLLM:
    name: str = "fake"
    model: str = "fake-1"
    next_packet: TriagePacket | None = None
    last_system: str = ""
    last_user: str = ""
    last_schema: Any = None
    call_count: int = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[Any] | None = None,
        max_output_tokens: int = 8192,
    ) -> Any:
        self.call_count += 1
        self.last_system = system
        self.last_user = user
        self.last_schema = response_schema
        if self.next_packet is None:
            raise LLMResponseError("FakeLLM has no next_packet configured")
        return self.next_packet


def _packet(case_id: str, disposition: TriageDisposition) -> TriagePacket:
    return TriagePacket(
        case_id=case_id,
        summary="synthetic test report",
        claim_type="path traversal",
        confidence=0.8,
        disposition=disposition,
        rationale="fake reasoning for the test",
    )


def _run_to_terminal(
    tmp_path: Path, raw_report: bytes, disposition: TriageDisposition
) -> tuple[Any, FakeLLM]:
    case_id = "case-triage-test"
    open_case(case_id=case_id, root=tmp_path, raw_report=raw_report)

    llm = FakeLLM(next_packet=_packet(case_id, disposition))
    registry = Registry()
    registry.register(TriageAgent(provider=llm))
    # noop_closer so TRIAGED reaches a terminal state (REJECTED) for the ADVANCE case.
    # TRIAGED->DONE was dropped (CLAUDE.md #8 — no shortcut past human review).
    registry.register(noop_closer)

    case = drive(case_id, registry, tmp_path)
    return case, llm


def test_advance_disposition_transitions_to_triaged_then_rejected(tmp_path: Path) -> None:
    case, llm = _run_to_terminal(tmp_path, b'{"id":"R1"}', TriageDisposition.ADVANCE)
    assert case.state == "REJECTED"
    assert llm.call_count == 1
    assert llm.last_schema is TriagePacket
    assert REPORT_DELIMITER in llm.last_user
    assert llm.last_system == SYSTEM_PROMPT


def test_reject_disposition_transitions_to_rejected(tmp_path: Path) -> None:
    case, _ = _run_to_terminal(tmp_path, b'{"id":"R2"}', TriageDisposition.REJECT_DUPLICATE)
    assert case.state == "REJECTED"


def test_triage_packet_artifact_persisted(tmp_path: Path) -> None:
    case_id = "case-triage-artifact"
    open_case(case_id=case_id, root=tmp_path, raw_report=b'{"id":"R3"}')

    llm = FakeLLM(next_packet=_packet(case_id, TriageDisposition.ADVANCE))
    registry = Registry()
    registry.register(TriageAgent(provider=llm))

    drive(case_id, registry, tmp_path)

    paths = case_root_paths(tmp_path, case_id)
    journal = Journal(paths["journal_dir"])
    entries = journal.read()
    transition = next(e for e in entries if e.kind == "transition")
    refs = transition.payload["artifacts"]
    assert any(a["kind"] == "triage_packet" for a in refs)


def test_llm_emitted_case_id_mismatch_is_overridden(tmp_path: Path) -> None:
    case_id = "case-mismatch"
    open_case(case_id=case_id, root=tmp_path, raw_report=b'{"id":"R4"}')

    # Provider returns a packet with the WRONG case_id — agent must override.
    bad_packet = TriagePacket(
        case_id="case-different",
        summary="x",
        claim_type="x",
        confidence=0.5,
        disposition=TriageDisposition.ADVANCE,
        rationale="x",
    )
    llm = FakeLLM(next_packet=bad_packet)

    registry = Registry()
    registry.register(TriageAgent(provider=llm))
    registry.register(noop_closer)

    case = drive(case_id, registry, tmp_path)
    assert case.state == "REJECTED"

    # Find the persisted triage_packet artifact and check case_id was overridden
    paths = case_root_paths(tmp_path, case_id)
    journal = Journal(paths["journal_dir"])
    store = ArtifactStore(paths["artifacts_dir"])
    transition = next(e for e in journal.read() if e.kind == "transition")
    packet_sha = next(
        a["id"] for a in transition.payload["artifacts"] if a["kind"] == "triage_packet"
    )
    persisted = TriagePacket.model_validate_json(store.get(packet_sha))
    assert persisted.case_id == case_id


def test_open_case_attaches_raw_report_artifact(tmp_path: Path) -> None:
    """Regression: P0 used to drop raw_report from Case.artifacts. The triage
    agent depends on it being attached."""
    case = open_case(
        case_id="case-raw", root=tmp_path, raw_report=b'{"id":"R5"}', raw_report_kind="raw_report"
    )
    assert len(case.artifacts) == 1
    assert case.artifacts[0].kind == "raw_report"


def test_canonical_json_round_trip_via_orchestrator(tmp_path: Path) -> None:
    """Sanity: the agent uses canonical_json so the artifact sha is stable
    across runs given identical packet content."""
    packet = TriagePacket(
        case_id="cx",
        summary="x",
        claim_type="x",
        confidence=0.5,
        disposition=TriageDisposition.ADVANCE,
        rationale="x",
    )
    bytes_a = canonical_json(packet.model_dump(mode="json"))
    bytes_b = canonical_json(packet.model_dump(mode="json"))
    assert bytes_a == bytes_b


def test_p0_hello_demo_still_passes(tmp_path: Path) -> None:
    """The Wave-A change to Case.artifacts (attaching raw_report) must not
    break the P0 hello demo or its replay-idempotence guarantee."""
    rc = cli_main(["hello", "--root", str(tmp_path)])
    assert rc == 0
    # Replay
    rc2 = cli_main(["hello", "--root", str(tmp_path)])
    assert rc2 == 0


def test_load_raw_report_helper_directly(tmp_path: Path) -> None:
    """Negative test: case with no raw_report artifact raises."""
    store = ArtifactStore(tmp_path / "artifacts")
    case = Case(
        id="c",
        state=str(State.INTAKE),
        created_at="2026-06-04T00:00:00.000000Z",
        artifacts=[],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="no raw_report"):
        _load_raw_report(case, ReadOnlyArtifactStore(store))
