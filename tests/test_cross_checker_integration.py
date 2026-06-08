# Integration tests for agents/cross_checker.py — real LLM call.
#
# Requires ANTHROPIC_API_KEY in the environment. Skipped cleanly when absent.
# Run with: pytest -v -m integration tests/test_cross_checker_integration.py
#
# T9 proof:
#   Positive control: correct plan for CWE-22 case -> cross-checker approves.
#   Negative control: CWE-89 SQLi plan fed to CWE-22 case -> cross-checker refuses.
# The negative control is the critical T9 test: if the cross-checker only rubber-stamps
# plans, it fails here by approving an obviously wrong fix.

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from patchwright.agents.cross_checker import CrossCheckerAgent
from patchwright.core.artifacts import ArtifactStore
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.models import Artifact, Case
from patchwright.models.patch_plan import PatchPlan
from patchwright.models.triage import TriageDisposition, TriagePacket
from patchwright.providers.anthropic_provider import AnthropicProvider

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "patch_corpus"

_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"


def _has_api_key() -> bool:
    return bool(os.environ.get(_ANTHROPIC_KEY_ENV))


skip_no_key = pytest.mark.skipif(
    not _has_api_key(),
    reason=f"integration: {_ANTHROPIC_KEY_ENV} not set",
)


# --------------------------------------------------------------------------- helpers


def _load_fixture_plan(fixture_name: str, case_id: str) -> PatchPlan:
    """Load a hand-authored plan from the patch corpus, overriding case_id."""
    raw = (FIXTURE_ROOT / fixture_name / "plan.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    data["case_id"] = case_id
    return PatchPlan.model_validate(data)


def _cwe22_triage_packet(case_id: str) -> TriagePacket:
    return TriagePacket(
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


def _make_case_with_packet_and_plan(
    tmp_path: Path,
    case_id: str,
    packet: TriagePacket,
    plan: PatchPlan,
) -> tuple[Case, ArtifactStore]:
    store = ArtifactStore(tmp_path / "artifacts")

    packet_bytes = canonical_json(packet.model_dump(mode="json"))
    plan_bytes = canonical_json(plan.model_dump(mode="json"))
    packet_sha = store.put(packet_bytes)
    plan_sha = store.put(plan_bytes)

    case = Case(
        id=case_id,
        state=str(State.PATCH_PROPOSED),
        created_at="2026-06-07T00:00:00.000000Z",
        artifacts=[
            Artifact(id=packet_sha, kind="triage_packet", size=len(packet_bytes)),
            Artifact(id=plan_sha, kind="patch_plan", size=len(plan_bytes)),
        ],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    return case, store


# --------------------------------------------------------------------------- positive control


@pytest.mark.integration
@skip_no_key
def test_cross_checker_approves_correct_cwe22_plan(tmp_path: Path) -> None:
    """Positive control: the hand-authored CWE-22 plan addresses the CWE-22 vulnerability.

    The cross-checker should approve. This confirms it can recognise a correct fix.
    """
    case_id = "integ-cc-cwe22-positive"
    packet = _cwe22_triage_packet(case_id)
    plan = _load_fixture_plan("cwe22_path_traversal", case_id)

    case, store = _make_case_with_packet_and_plan(tmp_path, case_id, packet, plan)

    provider = AnthropicProvider()
    agent = CrossCheckerAgent(provider=provider)
    result = agent(case, store.read_only())

    assert result.transition.from_state == str(State.PATCH_PROPOSED)
    # The correct plan must be approved — route to PATCH_APPLIED.
    assert result.transition.to_state == str(State.PATCH_APPLIED), (
        f"Cross-checker refused a CORRECT plan. Reasoning preserved in artifact. "
        f"Transition reason: {result.transition.reason!r}"
    )


# --------------------------------------------------------------------------- negative control


@pytest.mark.integration
@skip_no_key
def test_cross_checker_refuses_wrong_fixture_plan_for_cwe22(tmp_path: Path) -> None:
    """Negative control (critical T9 proof): feed a CWE-89 SQLi plan to a CWE-22 case.

    The CWE-89 plan replaces a SQL cursor.execute() call body — it says nothing
    about open() or path containment. The cross-checker must refuse this plan.

    If the cross-checker approves here, T9 mitigation has failed: it is rubber-stamping
    plans without actually checking them against the original report.

    Wrong-plan construction: we use the hand-authored cwe89_sqli/plan.json with
    case_id overridden to match the CWE-22 case. The plan's operations reference
    'get_user' and SQL parameterisation — unrelated to the 'read_file' path traversal.
    """
    case_id = "integ-cc-cwe22-negative"
    packet = _cwe22_triage_packet(case_id)

    # Intentionally wrong: SQLi plan applied to a path-traversal case.
    wrong_plan = _load_fixture_plan("cwe89_sqli", case_id)

    case, store = _make_case_with_packet_and_plan(tmp_path, case_id, packet, wrong_plan)

    provider = AnthropicProvider()
    agent = CrossCheckerAgent(provider=provider)
    result = agent(case, store.read_only())

    assert result.transition.from_state == str(State.PATCH_PROPOSED)
    # Wrong plan must be refused — route to REJECTED.
    assert result.transition.to_state == str(State.REJECTED), (
        f"Cross-checker APPROVED a plan that addresses the WRONG vulnerability. "
        f"T9 mitigation has failed. Transition reason: {result.transition.reason!r}"
    )
