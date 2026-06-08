"""Unit tests for agents/patch_plan.py — mocked LLM, no real API calls."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from patchwright.agents.patch_plan import (
    SNIPPET_DELIMITER,
    SYSTEM_PROMPT,
    VULN_DELIMITER,
    PatchPlanAgent,
    _build_user_message,
    _get_snippet,
    _imports_and_placeholder,
    _load_triage_packet,
)
from patchwright.core.artifacts import ArtifactStore, ReadOnlyArtifactStore
from patchwright.core.config import ConventionsConfig, PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json, sha256_b16
from patchwright.core.llm import LLMResponseError
from patchwright.core.models import Artifact, Case
from patchwright.models.patch_plan import InsertImport, PatchPlan
from patchwright.models.triage import TriageDisposition, TriagePacket

# --------------------------------------------------------------------------- fake provider


@dataclass
class FakeLLM:
    name: str = "fake"
    model: str = "fake-1"
    next_plan: PatchPlan | None = None
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
        max_output_tokens: int = 8192,
    ) -> Any:
        self.call_count += 1
        self.last_system = system
        self.last_user = user
        self.last_schema = response_schema
        if self.raise_error is not None:
            raise self.raise_error
        if self.next_plan is None:
            raise LLMResponseError("FakeLLM has no next_plan configured")
        return self.next_plan


# --------------------------------------------------------------------------- factories


def _make_triage_packet(case_id: str, component: str = "vulnerable.py") -> TriagePacket:
    return TriagePacket(
        case_id=case_id,
        summary="Path traversal in read_file",
        claim_type="path traversal",
        affected_components=[component],
        confidence=0.9,
        disposition=TriageDisposition.ADVANCE,
        rationale="User-supplied filename passed to open() unchecked.",
    )


def _make_patch_plan(case_id: str) -> PatchPlan:
    return PatchPlan(
        case_id=case_id,
        summary="Wrap open() with safe_path to prevent path traversal",
        operations=[
            InsertImport(
                file="vulnerable.py",
                module="patchwright_helpers",
                names=["safe_path"],
            ),
        ],
        rationale="safe_path checks the path is within the allowed base directory.",
    )


def _make_case(case_id: str, artifacts: list[Artifact]) -> Case:
    return Case(
        id=case_id,
        state=str(State.REPRODUCED),
        created_at="2026-06-07T00:00:00.000000Z",
        artifacts=artifacts,
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )


def _store_with_packet(
    tmp_path: Path, case_id: str, packet: TriagePacket
) -> tuple[ArtifactStore, ReadOnlyArtifactStore]:
    store = ArtifactStore(tmp_path / "artifacts")
    packet_bytes = canonical_json(packet.model_dump(mode="json"))
    sha = store.put(packet_bytes)
    _ = sha  # kept for side-effect (writing to store)
    return store, store.read_only()


def _artifact_for(packet: TriagePacket) -> Artifact:
    packet_bytes = canonical_json(packet.model_dump(mode="json"))
    return Artifact(id=sha256_b16(packet_bytes), kind="triage_packet", size=len(packet_bytes))


# --------------------------------------------------------------------------- prompt assembly


def test_prompt_contains_delimiter_wrapped_report(tmp_path: Path) -> None:
    packet = _make_triage_packet("case-x")
    snippet = "def read_file(filename):\n    return open(filename).read()"
    msg = _build_user_message("case-x", packet, snippet, "Project conventions: code_style='ruff'.")
    assert VULN_DELIMITER in msg
    assert SNIPPET_DELIMITER in msg
    assert "path traversal" in msg


def test_agent_does_not_import_secrets() -> None:
    """Structural: patch_plan.py must not import core.secrets (NFR-S-10).

    Secrets flow only into LLMProvider constructors, never through the agent
    layer. This asserts that by construction no secret-bearing object can reach
    the prompt — a real guarantee, unlike checking for a literal canary string
    that _build_user_message never receives anyway.
    """
    # Remove cached module so we get a fresh import graph inspection.
    mod_name = "patchwright.agents.patch_plan"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec = importlib.util.find_spec(mod_name)
    assert spec is not None
    assert spec.origin is not None

    source = Path(spec.origin).read_text(encoding="utf-8")
    assert "patchwright.core.secrets" not in source
    assert "from patchwright.core import secrets" not in source


def test_prompt_uses_correct_system_prompt_and_schema(tmp_path: Path) -> None:
    case_id = "case-prompt"
    packet = _make_triage_packet(case_id)
    _store, ro_store = _store_with_packet(tmp_path, case_id, packet)
    artifact = _artifact_for(packet)
    case = _make_case(case_id, artifacts=[artifact])

    llm = FakeLLM(next_plan=_make_patch_plan(case_id))
    agent = PatchPlanAgent(provider=llm, repo_root=tmp_path)
    agent(case, ro_store)

    assert llm.last_system == SYSTEM_PROMPT
    assert llm.last_schema is PatchPlan


# --------------------------------------------------------------------------- happy path


def test_happy_path_returns_patch_plan_artifact(tmp_path: Path) -> None:
    case_id = "case-happy"
    packet = _make_triage_packet(case_id)
    _store, ro_store = _store_with_packet(tmp_path, case_id, packet)
    artifact = _artifact_for(packet)
    case = _make_case(case_id, artifacts=[artifact])

    plan = _make_patch_plan(case_id)
    llm = FakeLLM(next_plan=plan)
    agent = PatchPlanAgent(provider=llm, repo_root=tmp_path)
    result = agent(case, ro_store)

    assert result.transition.from_state == str(State.REPRODUCED)
    assert result.transition.to_state == str(State.PATCH_PROPOSED)
    assert result.transition.case_id == case_id
    assert len(result.new_artifacts) == 1
    _bytes, kind = result.new_artifacts[0]
    assert kind == "patch_plan"
    recovered = PatchPlan.model_validate_json(_bytes)
    assert recovered.case_id == case_id
    assert recovered.summary == plan.summary


def test_case_id_mismatch_overridden(tmp_path: Path) -> None:
    case_id = "case-correct"
    packet = _make_triage_packet(case_id)
    _store, ro_store = _store_with_packet(tmp_path, case_id, packet)
    artifact = _artifact_for(packet)
    case = _make_case(case_id, artifacts=[artifact])

    bad_plan = _make_patch_plan("case-wrong")
    llm = FakeLLM(next_plan=bad_plan)
    agent = PatchPlanAgent(provider=llm, repo_root=tmp_path)
    result = agent(case, ro_store)

    _bytes, _ = result.new_artifacts[0]
    recovered = PatchPlan.model_validate_json(_bytes)
    assert recovered.case_id == case_id


# --------------------------------------------------------------------------- error handling


def test_invalid_llm_output_raises_llm_response_error(tmp_path: Path) -> None:
    case_id = "case-bad"
    packet = _make_triage_packet(case_id)
    _store, ro_store = _store_with_packet(tmp_path, case_id, packet)
    artifact = _artifact_for(packet)
    case = _make_case(case_id, artifacts=[artifact])

    llm = FakeLLM(raise_error=LLMResponseError("provider returned malformed JSON"))
    agent = PatchPlanAgent(provider=llm, repo_root=tmp_path)

    with pytest.raises(LLMResponseError, match="malformed JSON"):
        agent(case, ro_store)


def test_missing_triage_packet_raises_value_error(tmp_path: Path) -> None:
    """Agent must raise clearly when there's no triage_packet artifact."""
    store = ArtifactStore(tmp_path / "artifacts")
    ro_store = store.read_only()
    case = _make_case("case-empty", artifacts=[])

    llm = FakeLLM(next_plan=_make_patch_plan("case-empty"))
    agent = PatchPlanAgent(provider=llm, repo_root=tmp_path)

    with pytest.raises(ValueError, match="no triage_packet"):
        agent(case, ro_store)


# --------------------------------------------------------------------------- _load_triage_packet


def test_load_triage_packet_helper(tmp_path: Path) -> None:
    case_id = "case-ltp"
    packet = _make_triage_packet(case_id)
    _store, ro_store = _store_with_packet(tmp_path, case_id, packet)
    artifact = _artifact_for(packet)
    case = _make_case(case_id, artifacts=[artifact])

    loaded = _load_triage_packet(case, ro_store)
    assert loaded.case_id == case_id
    assert loaded.claim_type == "path traversal"


# ---------------------------------------------------------------- path-traversal containment


def test_get_snippet_rejects_path_traversal(tmp_path: Path) -> None:
    """A traversal component (e.g. '../../../etc/passwd') must be skipped, not read."""
    # Ensure the target outside repo_root exists so the only reason it's skipped
    # is the containment check, not file-not-found.
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("s3cr3t", encoding="utf-8")

    packet = _make_triage_packet("case-trav", component="../secret.txt::read_file")
    # _get_snippet must not raise, must not return the secret content.
    result = _get_snippet(packet, tmp_path)
    assert "s3cr3t" not in result
    assert result == "(no code snippet available)"


def test_get_snippet_accepts_legitimate_in_repo_path(tmp_path: Path) -> None:
    """A valid in-repo file path succeeds and its content is returned."""
    legitimate = tmp_path / "mymodule.py"
    legitimate.write_text("def hello(): pass\n", encoding="utf-8")

    packet = _make_triage_packet("case-legit", component="mymodule.py")
    result = _get_snippet(packet, tmp_path)
    assert "hello" in result


# ---------------------------------------------------------------- fallback: imports + placeholder


def test_get_snippet_missing_symbol_returns_imports_and_placeholder(tmp_path: Path) -> None:
    """When a symbol is not found, fallback returns imports + placeholder, not function bodies."""
    src = "import os\nfrom pathlib import Path\n\ndef real_func():\n    return 1\n"
    (tmp_path / "mod.py").write_text(src, encoding="utf-8")

    packet = _make_triage_packet("case-fb", component="mod.py::no_such_symbol")
    result = _get_snippet(packet, tmp_path)

    assert "import os" in result
    assert "no_such_symbol" in result  # placeholder names the missing symbol
    assert "real_func" not in result  # no function body leaked


def test_get_snippet_missing_symbol_no_imports_returns_just_placeholder(tmp_path: Path) -> None:
    """File with no imports produces only the placeholder, no function bodies."""
    src = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
    (tmp_path / "noimp.py").write_text(src, encoding="utf-8")

    packet = _make_triage_packet("case-noimp", component="noimp.py::ghost")
    result = _get_snippet(packet, tmp_path)

    assert "ghost" in result
    assert "alpha" not in result
    assert "beta" not in result


def test_imports_and_placeholder_direct(tmp_path: Path) -> None:
    """Unit-test _imports_and_placeholder directly."""
    src = "import re\n\ndef do_stuff(): pass\n"
    p = tmp_path / "x.py"
    p.write_text(src, encoding="utf-8")

    result = _imports_and_placeholder(p, "missing_fn")
    assert "import re" in result
    assert "missing_fn" in result
    assert "do_stuff" not in result


# --------------------------------------------------------------------------- conventions in prompt


def test_conventions_appear_in_user_message(tmp_path: Path) -> None:
    config = PatchwrightConfig()
    config = config.model_copy(
        update={"conventions": ConventionsConfig(code_style="black", test_command="pytest -x")}
    )
    packet = _make_triage_packet("case-conv")
    snippet = "def read_file(f): pass"
    msg = _build_user_message(
        "case-conv",
        packet,
        snippet,
        "code_style='black' test_command='pytest -x'",
    )
    assert "black" in msg
    assert "pytest -x" in msg
