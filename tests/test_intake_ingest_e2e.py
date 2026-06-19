"""End-to-end ingest() test (AEG-444 / AEG-377 exit criterion).

Drops the GHSA fixture into `ingest()` and asserts:

  1. The Case is opened in INTAKE state.
  2. Three artifacts attach: raw_input, raw_report, reporter_identity.
  3. T10 invariant: real reporter PII never appears in the raw_report
     artifact bytes — but the pseudonymous reporter_id does.
  4. ingest() is idempotent on duplicate drops of the same advisory
     (case_id derived from source_id by default).
"""

from __future__ import annotations

import json
from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.fsm import INITIAL_STATE
from patchwright.core.intake import ingest
from patchwright.core.orchestrator import case_root_paths

FIXTURE = Path(__file__).parent / "fixtures" / "intake" / "sample_ghsa.json"

# Hard-coded PII strings from the fixture — if the fixture changes, update
# these too. The T10 invariant test checks each one is absent from the
# normalized raw_report.
_REAL_EMAIL = b"alice@example.com"
_REAL_HANDLE = b"alice-researcher"
_REAL_NAME = b"Alice Q. Researcher"


def _artifact_by_kind(case_artifacts: list, kind: str):
    matches = [a for a in case_artifacts if a.kind == kind]
    assert len(matches) == 1, f"expected exactly one {kind} artifact, got {len(matches)}"
    return matches[0]


def _read_artifact(root: Path, artifact_id: str) -> bytes:
    return ArtifactStore(root / "artifacts").get(artifact_id)


def test_ghsa_ingest_opens_case_in_intake_state(tmp_path: Path) -> None:
    case = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path)

    assert case.state == str(INITIAL_STATE) == "INTAKE"
    # All three artifacts attached on case_opened.
    kinds = sorted(a.kind for a in case.artifacts)
    assert kinds == ["raw_input", "raw_report", "reporter_identity"]


def test_ghsa_ingest_preserves_raw_input_bytes(tmp_path: Path) -> None:
    """FR-IN-2: operator's original bytes are preserved verbatim."""
    raw = FIXTURE.read_bytes()
    case = ingest(raw, source="ghsa", root=tmp_path)

    raw_input = _artifact_by_kind(case.artifacts, "raw_input")
    assert _read_artifact(tmp_path, raw_input.id) == raw


def test_ghsa_ingest_raw_report_is_canonical_json(tmp_path: Path) -> None:
    """raw_report is the normalized OSV-shaped Report, ready for triage."""
    case = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path)
    raw_report = _artifact_by_kind(case.artifacts, "raw_report")

    payload = json.loads(_read_artifact(tmp_path, raw_report.id))
    assert payload["source_id"] == "ghsa:GHSA-rprw-h62v-c2w7"
    assert payload["source_adapter"] == "ghsa"
    assert payload["summary"] == "PyYAML insecure deserialization via yaml.load"


def test_ghsa_ingest_reporter_identity_stored_separately(tmp_path: Path) -> None:
    case = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path)
    identity_artifact = _artifact_by_kind(case.artifacts, "reporter_identity")

    payload = json.loads(_read_artifact(tmp_path, identity_artifact.id))
    assert payload["real_email"] == "alice@example.com"
    assert payload["real_handle"] == "@alice-researcher"
    assert payload["real_name"] == "Alice Q. Researcher"


def test_t10_real_pii_never_in_raw_report_bytes(tmp_path: Path) -> None:
    """The load-bearing T10 invariant for the whole intake pipeline:
    serialized raw_report — what triage and the journal read — must not
    contain the reporter's real handle, name, or email. Those live only
    in the separable reporter_identity artifact, which M3-encrypt (AEG-376)
    will wrap with at-rest encryption."""
    case = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path)
    raw_report = _artifact_by_kind(case.artifacts, "raw_report")
    report_bytes = _read_artifact(tmp_path, raw_report.id)

    assert _REAL_EMAIL not in report_bytes
    assert _REAL_HANDLE not in report_bytes
    assert _REAL_NAME not in report_bytes


def test_t10_pseudonymous_reporter_id_does_appear_in_raw_report(tmp_path: Path) -> None:
    """Counterpart to the negative T10 test: the pseudonym IS present, so
    rule-based trust scoring (FR-TR-2) can still correlate reports from
    the same reporter without ever touching real identity."""
    case = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path)
    raw_report = _artifact_by_kind(case.artifacts, "raw_report")
    report_bytes = _read_artifact(tmp_path, raw_report.id)

    payload = json.loads(report_bytes)
    pseudonym = payload["reporter_id"]
    assert pseudonym.startswith("reporter-")
    assert pseudonym.encode("utf-8") in report_bytes


def test_ingest_is_idempotent_on_duplicate_drops(tmp_path: Path) -> None:
    """Same source_id → same case_id → second ingest() returns the existing
    Case unchanged. Lets operators safely re-drop a payload without forking
    a duplicate case."""
    raw = FIXTURE.read_bytes()
    first = ingest(raw, source="ghsa", root=tmp_path)
    second = ingest(raw, source="ghsa", root=tmp_path)

    assert first.id == second.id
    assert first.last_seq == second.last_seq
    assert first.last_hash == second.last_hash


def test_ingest_persists_to_expected_layout(tmp_path: Path) -> None:
    """The on-disk layout is what `patchwright list` / `explain` / future
    `review` commands expect: journal/<case-id>/journal.jsonl + artifacts/."""
    case = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path)
    paths = case_root_paths(tmp_path, case.id)

    assert paths["journal_dir"].is_dir()
    assert paths["journal_file"].is_file()
    assert paths["artifacts_dir"].is_dir()


def test_json_source_also_works(tmp_path: Path) -> None:
    """Smoke test that ingest() routes through the json adapter too — same
    invariants as ghsa, just exercising the other adapter."""
    osv_payload = {
        "id": "PYSEC-2024-1",
        "summary": "test",
        "affected": [{"package": {"ecosystem": "PyPI", "name": "foo"}}],
    }
    case = ingest(
        json.dumps(osv_payload).encode("utf-8"),
        source="json",
        root=tmp_path,
    )
    assert case.state == "INTAKE"
    kinds = sorted(a.kind for a in case.artifacts)
    # No reporter_identity here because the payload has no credits / reporter.
    assert kinds == ["raw_input", "raw_report"]


def test_explicit_case_id_seed_overrides_source_id(tmp_path: Path) -> None:
    """Operators can force a stable case_id for tests / reruns by passing a seed."""
    case_a = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path, case_id_seed=b"seed-A")
    case_b = ingest(FIXTURE.read_bytes(), source="ghsa", root=tmp_path, case_id_seed=b"seed-B")
    assert case_a.id != case_b.id
