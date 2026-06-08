"""Integration tests — real LLM call + codemod round-trip for each CWE fixture.

Requires ANTHROPIC_API_KEY in the environment (or OS keychain). Skipped
automatically when the key is absent. Run with:

    pytest -v -m integration tests/test_patch_plan_integration.py

Exit criterion (phase1-work-plan.md Wave B M2-plan): for each of the 3 CWE
fixtures, the LLM produces a PatchPlan that, when fed to codemod_python.apply,
yields code that fixes the vulnerability (verified via semantic assertions, not
byte-equality — the LLM plan need not match the hand-authored plan.json).

CWE-22 (path traversal) is the canary: it must pass for the task to be
considered complete. CWE-89 and CWE-502 are opt-in but run in the same mark.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from patchwright.agents.patch_plan import PatchPlanAgent
from patchwright.core.artifacts import ArtifactStore
from patchwright.core.hashing import canonical_json
from patchwright.core.models import Artifact, Case
from patchwright.models.patch_plan import PatchPlan
from patchwright.models.triage import TriageDisposition, TriagePacket
from patchwright.providers.anthropic_provider import AnthropicProvider
from patchwright.tools.codemod_python import apply

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "patch_corpus"

_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"


def _has_api_key() -> bool:
    return bool(os.environ.get(_ANTHROPIC_KEY_ENV))


skip_no_key = pytest.mark.skipif(
    not _has_api_key(),
    reason=f"integration: {_ANTHROPIC_KEY_ENV} not set",
)


# --------------------------------------------------------------------------- helpers


def _make_case_with_packet(
    tmp_path: Path,
    case_id: str,
    packet: TriagePacket,
) -> tuple[Case, ArtifactStore]:
    store = ArtifactStore(tmp_path / "artifacts")
    packet_bytes = canonical_json(packet.model_dump(mode="json"))
    sha = store.put(packet_bytes)
    artifact = Artifact(id=sha, kind="triage_packet", size=len(packet_bytes))
    case = Case(
        id=case_id,
        state="REPRODUCED",
        created_at="2026-06-07T00:00:00.000000Z",
        artifacts=[artifact],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    return case, store


def _run_agent(case: Case, store: ArtifactStore, repo_root: Path) -> PatchPlan:
    """Invoke PatchPlanAgent with a real AnthropicProvider and return the plan."""
    provider = AnthropicProvider()
    agent = PatchPlanAgent(provider=provider, repo_root=repo_root)
    result = agent(case, store.read_only())
    _bytes, kind = result.new_artifacts[0]
    assert kind == "patch_plan"
    return PatchPlan.model_validate_json(_bytes)


def _make_repo(tmp_path: Path, fixture_name: str) -> Path:
    """Copy the fixture's vulnerable.py into a fresh temp repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(FIXTURE_ROOT / fixture_name / "vulnerable.py", repo / "vulnerable.py")
    return repo


# --------------------------------------------------------------------------- CWE-22 path traversal


@pytest.mark.integration
@skip_no_key
def test_cwe22_path_traversal_round_trip(tmp_path: Path) -> None:
    """Canary: LLM plan applied by codemod must neutralize open(user_input)."""
    case_id = "integ-cwe22"
    repo = _make_repo(tmp_path, "cwe22_path_traversal")

    packet = TriagePacket(
        case_id=case_id,
        summary="User-supplied filename passed directly to open() in read_file()",
        claim_type="path traversal",
        affected_components=["vulnerable.py::read_file"],
        confidence=0.95,
        disposition=TriageDisposition.ADVANCE,
        rationale=(
            "The function read_file(filename) calls open(filename) without "
            "sanitising or resolving the path. An attacker can pass "
            "'../../../etc/passwd' to read arbitrary files. CWE-22."
        ),
    )

    case, store = _make_case_with_packet(tmp_path, case_id, packet)
    plan = _run_agent(case, store, repo)

    assert plan.case_id == case_id
    assert len(plan.operations) >= 1

    modified = apply(plan, repo)
    patched = next(iter(modified.values()))

    # The raw open(filename) call must be wrapped or the function body replaced.
    bare_open = re.search(r"\bopen\(\s*filename\s*\)", patched)
    assert bare_open is None, "open(filename) is still unguarded in patched output"


# --------------------------------------------------------------------------- CWE-89 SQL injection


@pytest.mark.integration
@skip_no_key
def test_cwe89_sqli_round_trip(tmp_path: Path) -> None:
    """LLM plan applied by codemod must eliminate f-string SQL interpolation."""
    case_id = "integ-cwe89"
    repo = _make_repo(tmp_path, "cwe89_sqli")

    packet = TriagePacket(
        case_id=case_id,
        summary="SQL injection via f-string in get_user()",
        claim_type="sql injection",
        affected_components=["vulnerable.py::get_user"],
        confidence=0.95,
        disposition=TriageDisposition.ADVANCE,
        rationale=(
            "cursor.execute() is called with an f-string that embeds the username "
            "directly. A payload like `x' OR '1'='1` bypasses authentication. CWE-89."
        ),
    )

    case, store = _make_case_with_packet(tmp_path, case_id, packet)
    plan = _run_agent(case, store, repo)

    assert plan.case_id == case_id
    modified = apply(plan, repo)
    patched = next(iter(modified.values()))

    # The f-string SQL interpolation must be gone.
    assert 'f"SELECT' not in patched and "f'SELECT" not in patched
    # Parameterized form should be present.
    assert "?" in patched or "%s" in patched


# --------------------------------------------------------------------------- CWE-502


@pytest.mark.integration
@skip_no_key
def test_cwe502_deserialization_round_trip(tmp_path: Path) -> None:
    """LLM plan applied by codemod must remove or replace pickle.loads."""
    case_id = "integ-cwe502"
    repo = _make_repo(tmp_path, "cwe502_deserialization")

    packet = TriagePacket(
        case_id=case_id,
        summary="Insecure deserialization via pickle.loads in load_config()",
        claim_type="insecure deserialization",
        affected_components=["vulnerable.py::load_config"],
        confidence=0.95,
        disposition=TriageDisposition.ADVANCE,
        rationale=(
            "load_config(data) passes untrusted bytes to pickle.loads(). "
            "An attacker-controlled payload can achieve arbitrary code execution. CWE-502."
        ),
    )

    case, store = _make_case_with_packet(tmp_path, case_id, packet)
    plan = _run_agent(case, store, repo)

    assert plan.case_id == case_id
    modified = apply(plan, repo)
    patched = next(iter(modified.values()))

    # pickle.loads on untrusted data must be gone or replaced.
    assert "pickle.loads(data)" not in patched
