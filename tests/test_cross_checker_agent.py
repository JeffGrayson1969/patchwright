"""Unit tests for agents/cross_checker.py — mocked LLM, no real API calls.

Covers:
  - Happy path: approve verdict routes to PATCH_APPLIED
  - Refuse path: refuse verdict routes to REJECTED
  - Prompt contains delimiter-wrapped original report AND candidate PatchPlan
  - Malformed LLM output raises a clear named error (no swallow)
  - Prompt assembly is deterministic (same inputs -> same prompt)
  - cross_checker.py does not import core.secrets (NFR-S-10 structural check)
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from patchwright.agents.cross_checker import (
    PLAN_DELIMITER,
    REPORT_DELIMITER,
    SKEPTIC_SYSTEM_PROMPT,
    CrossCheckerAgent,
    _build_user_message,
    _load_patch_plan,
    _load_triage_packet,
)
from patchwright.core.artifacts import ArtifactStore, ReadOnlyArtifactStore
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.llm import LLMResponseError
from patchwright.core.models import Artifact, Case
from patchwright.models.cross_check import CrossCheckVerdict
from patchwright.models.patch_plan import InsertImport, PatchPlan
from patchwright.models.triage import TriageDisposition, TriagePacket

# --------------------------------------------------------------------------- fake provider


@dataclass
class FakeLLM:
    name: str = "fake"
    model: str = "fake-1"
    next_verdict: CrossCheckVerdict | None = None
    raise_error: Exception | None = None
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
        max_output_tokens: int = 4096,
    ) -> Any:
        self.call_count += 1
        self.last_system = system
        self.last_user = user
        self.last_schema = response_schema
        if self.raise_error is not None:
            raise self.raise_error
        if self.next_verdict is None:
            raise LLMResponseError("FakeLLM has no next_verdict configured")
        return self.next_verdict


# --------------------------------------------------------------------------- factories


def _make_triage_packet(case_id: str) -> TriagePacket:
    return TriagePacket(
        case_id=case_id,
        summary="Path traversal in read_file via user-supplied filename",
        claim_type="path traversal",
        affected_components=["vulnerable.py::read_file"],
        confidence=0.9,
        disposition=TriageDisposition.ADVANCE,
        rationale="User-supplied filename passed directly to open() with no containment check.",
    )


def _make_patch_plan(case_id: str) -> PatchPlan:
    return PatchPlan(
        case_id=case_id,
        summary="Wrap open() with safe_path to prevent path traversal",
        operations=[
            InsertImport(file="vulnerable.py", module="patchwright_helpers", names=["safe_path"]),
        ],
        rationale="safe_path checks the path is within the allowed base directory.",
    )


def _make_approve_verdict() -> CrossCheckVerdict:
    return CrossCheckVerdict(
        vulnerability_summary="User input flows to open() without path containment.",
        fix_summary="Wraps the open() call with safe_path which enforces a base directory.",
        verdict="approve",
        reasoning="The fix directly closes the unsafe open() call with a path validator.",
        confidence=0.95,
    )


def _make_refuse_verdict(reason: str = "Fix addresses wrong vulnerability") -> CrossCheckVerdict:
    return CrossCheckVerdict(
        vulnerability_summary="User input flows to open() without path containment.",
        fix_summary="Wraps subprocess.run() call — unrelated to the reported open() sink.",
        verdict="refuse",
        reasoning=reason,
        confidence=0.88,
    )


def _make_case(
    case_id: str,
    store: ArtifactStore,
    packet: TriagePacket,
    plan: PatchPlan,
) -> tuple[Case, ReadOnlyArtifactStore]:
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
    return case, store.read_only()


def _default_case(case_id: str, tmp_path: Path) -> tuple[Case, ReadOnlyArtifactStore]:
    store = ArtifactStore(tmp_path / "artifacts")
    return _make_case(case_id, store, _make_triage_packet(case_id), _make_patch_plan(case_id))


# --------------------------------------------------------------------------- happy path: approve


def test_approve_verdict_routes_to_patch_applied(tmp_path: Path) -> None:
    case_id = "case-approve"
    case, ro_store = _default_case(case_id, tmp_path)

    llm = FakeLLM(next_verdict=_make_approve_verdict())
    agent = CrossCheckerAgent(provider=llm)
    result = agent(case, ro_store)

    assert result.transition.from_state == str(State.PATCH_PROPOSED)
    assert result.transition.to_state == str(State.PATCH_APPLIED)
    assert result.transition.case_id == case_id
    assert llm.call_count == 1


def test_approve_verdict_artifact_round_trips(tmp_path: Path) -> None:
    case_id = "case-approve-rt"
    case, ro_store = _default_case(case_id, tmp_path)

    expected = _make_approve_verdict()
    llm = FakeLLM(next_verdict=expected)
    agent = CrossCheckerAgent(provider=llm)
    result = agent(case, ro_store)

    assert len(result.new_artifacts) == 1
    artifact_bytes, kind = result.new_artifacts[0]
    assert kind == "cross_check_verdict"
    recovered = CrossCheckVerdict.model_validate_json(artifact_bytes)
    assert recovered.verdict == "approve"
    assert recovered.confidence == pytest.approx(expected.confidence)
    assert recovered.reasoning == expected.reasoning


# --------------------------------------------------------------------------- refuse path


def test_refuse_verdict_routes_to_rejected(tmp_path: Path) -> None:
    case_id = "case-refuse"
    case, ro_store = _default_case(case_id, tmp_path)

    llm = FakeLLM(next_verdict=_make_refuse_verdict())
    agent = CrossCheckerAgent(provider=llm)
    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.REJECTED)


def test_refuse_reasoning_preserved_in_artifact(tmp_path: Path) -> None:
    case_id = "case-refuse-rt"
    case, ro_store = _default_case(case_id, tmp_path)

    reason = "Plan wraps subprocess.run instead of the vulnerable open() call"
    llm = FakeLLM(next_verdict=_make_refuse_verdict(reason))
    agent = CrossCheckerAgent(provider=llm)
    result = agent(case, ro_store)

    artifact_bytes, _ = result.new_artifacts[0]
    recovered = CrossCheckVerdict.model_validate_json(artifact_bytes)
    assert recovered.reasoning == reason
    assert recovered.verdict == "refuse"


# --------------------------------------------------------------------------- prompt assertions


def test_prompt_contains_delimiter_wrapped_report_and_plan() -> None:
    """The cross-checker prompt MUST include both the original report (delimiter-wrapped)
    AND the candidate PatchPlan (delimiter-wrapped). Without both, T9 is not exercised —
    the checker would be blind to either the vulnerability or the fix."""
    packet = _make_triage_packet("case-prompt")
    plan = _make_patch_plan("case-prompt")
    msg = _build_user_message("case-prompt", packet, plan)

    assert REPORT_DELIMITER in msg
    assert PLAN_DELIMITER in msg
    # The original vulnerability claim must be present in the report section.
    assert "path traversal" in msg
    # The plan's summary/rationale must be present in the plan section.
    assert "safe_path" in msg


def test_prompt_uses_skeptic_system_prompt(tmp_path: Path) -> None:
    case_id = "case-sys"
    case, ro_store = _default_case(case_id, tmp_path)

    llm = FakeLLM(next_verdict=_make_approve_verdict())
    agent = CrossCheckerAgent(provider=llm)
    agent(case, ro_store)

    assert llm.last_system == SKEPTIC_SYSTEM_PROMPT
    # Skeptic system prompt must be meaningfully different from patch_plan's constructive framing.
    assert "NOT the author" in llm.last_system
    assert "find flaws" in llm.last_system


def test_agent_does_not_import_secrets() -> None:
    """Structural: cross_checker.py must not import core.secrets (NFR-S-10).

    Secrets flow into LLMProvider constructors only, never through the agent layer.
    """
    mod_name = "patchwright.agents.cross_checker"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec = importlib.util.find_spec(mod_name)
    assert spec is not None and spec.origin is not None

    source = Path(spec.origin).read_text(encoding="utf-8")
    assert "patchwright.core.secrets" not in source
    assert "from patchwright.core import secrets" not in source


# --------------------------------------------------------------------------- error handling


def test_malformed_llm_output_raises_named_error(tmp_path: Path) -> None:
    """Provider returning invalid output (LLMResponseError) must not be swallowed."""
    case_id = "case-bad"
    case, ro_store = _default_case(case_id, tmp_path)

    llm = FakeLLM(raise_error=LLMResponseError("provider returned malformed JSON"))
    agent = CrossCheckerAgent(provider=llm)

    with pytest.raises(LLMResponseError, match="malformed JSON"):
        agent(case, ro_store)


def test_wrong_return_type_raises_llm_response_error_not_attribute_error(tmp_path: Path) -> None:
    """isinstance guard: provider returning str (not CrossCheckVerdict) must raise
    LLMResponseError with a clear message, NOT AttributeError from .verdict access."""
    case_id = "case-wrong-type"
    case, ro_store = _default_case(case_id, tmp_path)

    @dataclass
    class StrReturningLLM:
        name: str = "str-returner"
        model: str = "fake-str-1"

        def complete(
            self,
            *,
            system: str,
            user: str,
            response_schema: Any = None,
            max_output_tokens: int = 4096,
        ) -> Any:
            return '{"verdict": "approve"}'  # raw str, not CrossCheckVerdict

    agent = CrossCheckerAgent(provider=StrReturningLLM())
    with pytest.raises(LLMResponseError, match="cross_checker expected CrossCheckVerdict"):
        agent(case, ro_store)


def test_missing_triage_packet_raises_value_error(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    ro_store = store.read_only()
    plan = _make_patch_plan("case-nopkt")
    plan_bytes = canonical_json(plan.model_dump(mode="json"))
    plan_sha = store.put(plan_bytes)

    case = Case(
        id="case-nopkt",
        state=str(State.PATCH_PROPOSED),
        created_at="2026-06-07T00:00:00.000000Z",
        artifacts=[Artifact(id=plan_sha, kind="patch_plan", size=len(plan_bytes))],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="no triage_packet"):
        _load_triage_packet(case, ro_store)


def test_missing_patch_plan_raises_value_error(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    ro_store = store.read_only()
    packet = _make_triage_packet("case-noplan")
    packet_bytes = canonical_json(packet.model_dump(mode="json"))
    packet_sha = store.put(packet_bytes)

    case = Case(
        id="case-noplan",
        state=str(State.PATCH_PROPOSED),
        created_at="2026-06-07T00:00:00.000000Z",
        artifacts=[Artifact(id=packet_sha, kind="triage_packet", size=len(packet_bytes))],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="no patch_plan"):
        _load_patch_plan(case, ro_store)


# --------------------------------------------------------------------------- prompt determinism


def test_prompt_assembly_is_deterministic() -> None:
    """Given the same inputs twice, _build_user_message must produce the same bytes.

    Pydantic dicts can iterate in insertion order; json.dumps(sort_keys=True) for
    the plan prevents nondeterminism from dict ordering.
    """
    packet = _make_triage_packet("case-det")
    plan = _make_patch_plan("case-det")

    msg_a = _build_user_message("case-det", packet, plan)
    msg_b = _build_user_message("case-det", packet, plan)

    assert msg_a == msg_b


# --------------------------------------------------------------------------- registry


def test_cross_checker_in_default_registry() -> None:
    """The default_registry does NOT include cross_checker (handles noop demo flow),
    but cross_checker_registry DOES include an agent for PATCH_PROPOSED."""
    from patchwright.core.registry import cross_checker_registry, default_registry  # noqa: PLC0415

    dr = default_registry()
    assert dr.agent_for_state(str(State.PATCH_PROPOSED)) is None

    # cross_checker_registry needs provider objects; use FakeLLM (satisfies Protocol).
    llm_primary = FakeLLM()
    llm_cc = FakeLLM()
    ccr = cross_checker_registry(llm_primary, llm_cc, Path("/tmp"))
    agent = ccr.agent_for_state(str(State.PATCH_PROPOSED))
    assert agent is not None
    assert agent.name == "cross_checker"
    assert agent.handles_state == str(State.PATCH_PROPOSED)
