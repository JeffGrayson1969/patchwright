# Integration tests — real LLM call + codemod round-trip for each CWE fixture.
#
# Requires ANTHROPIC_API_KEY in the environment. Skipped automatically when
# absent. Run with: pytest -v -m integration tests/test_patch_plan_integration.py
#
# Exit criterion (phase1-work-plan.md Wave B M2-plan): for each of the 3 CWE
# fixtures, the LLM produces a PatchPlan that, when fed to codemod_python.apply,
# yields a patched file where:
#   (a) the generated regression test FAILS against the vulnerable code, and
#   (b) the generated regression test PASSES against the patched code.
# If the LLM omits test_spec, semantic assertions on the patch text are used
# as a fallback.
#
# CWE-22 (path traversal) is the canary: must pass for the task to be complete.

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from patchwright.agents.patch_plan import PatchPlanAgent
from patchwright.core.artifacts import ArtifactStore
from patchwright.core.hashing import canonical_json
from patchwright.core.models import Artifact, Case
from patchwright.models.patch_plan import PatchPlan, TestSpec
from patchwright.models.triage import TriageDisposition, TriagePacket
from patchwright.providers.anthropic_provider import AnthropicProvider
from patchwright.tools.codemod_python import apply
from patchwright.tools.test_gen_python import render

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "patch_corpus"

_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

_SUBPROCESS_TIMEOUT = 60  # seconds


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
    provider = AnthropicProvider()
    agent = PatchPlanAgent(provider=provider, repo_root=repo_root)
    result = agent(case, store.read_only())
    _bytes, kind = result.new_artifacts[0]
    assert kind == "patch_plan"
    return PatchPlan.model_validate_json(_bytes)


def _make_repo(tmp_path: Path, fixture_name: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(FIXTURE_ROOT / fixture_name / "vulnerable.py", repo / "vulnerable.py")
    return repo


def _run_pytest_in(test_file: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run pytest on a single test file. Raises TimeoutExpired on timeout."""
    minimal_env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "VIRTUAL_ENV", "UV_PROJECT"}
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "--tb=short"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        env=minimal_env,
        check=False,
    )


def _run_regression_suite(
    spec: TestSpec,
    vulnerable_src: str,
    patched_src: str,
    tmp_path: Path,
) -> None:
    """Write vulnerable/patched files and run the generated test against each.

    The spec's target_import is used as the module filename. If the LLM chose
    something other than 'vulnerable', we still honour it — both source files
    are written under that name so the import resolves in the subprocess cwd.
    """
    test_src = render(spec)
    test_file = tmp_path / "test_regression.py"
    test_file.write_text(test_src, encoding="utf-8")

    # The module the test imports from.
    module_file = tmp_path / f"{spec.target_import.split('.')[-1]}.py"

    # --- round-trip A: test must FAIL on vulnerable code ---
    module_file.write_text(vulnerable_src, encoding="utf-8")
    try:
        result_vuln = _run_pytest_in(test_file, tmp_path)
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"pytest subprocess timed out after {_SUBPROCESS_TIMEOUT}s "
            "running regression test against vulnerable code"
        )
    assert result_vuln.returncode != 0, (
        "Regression test should FAIL on vulnerable code but it passed.\n"
        f"stdout:\n{result_vuln.stdout}\nstderr:\n{result_vuln.stderr}"
    )

    # --- round-trip B: test must PASS on patched code ---
    module_file.write_text(patched_src, encoding="utf-8")
    try:
        result_patched = _run_pytest_in(test_file, tmp_path)
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"pytest subprocess timed out after {_SUBPROCESS_TIMEOUT}s "
            "running regression test against patched code"
        )
    assert result_patched.returncode == 0, (
        "Regression test should PASS on patched code but it failed.\n"
        f"stdout:\n{result_patched.stdout}\nstderr:\n{result_patched.stderr}"
    )


# --------------------------------------------------------------------------- CWE-22 path traversal


@pytest.mark.integration
@skip_no_key
def test_cwe22_path_traversal_round_trip(tmp_path: Path) -> None:
    """Canary: LLM plan applied by codemod must neutralize open(user_input).

    If the LLM emits a test_spec, the generated regression test is run via
    pytest subprocess against both vulnerable and patched code (exit criterion
    per phase1-work-plan.md Wave B M2-plan).
    """
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

    vulnerable_src = (repo / "vulnerable.py").read_text(encoding="utf-8")
    modified = apply(plan, repo)
    patched_src = next(iter(modified.values()))

    # Semantic assertion: the bare open(filename) call must be gone.
    bare_open = re.search(r"\bopen\(\s*filename\s*\)", patched_src)
    assert bare_open is None, "open(filename) is still unguarded in patched output"

    # Exit-gate: run the generated regression test if the LLM produced a spec.
    if plan.test_spec is not None:
        suite_dir = tmp_path / "suite_cwe22"
        suite_dir.mkdir()
        _run_regression_suite(plan.test_spec, vulnerable_src, patched_src, suite_dir)


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

    vulnerable_src = (repo / "vulnerable.py").read_text(encoding="utf-8")
    modified = apply(plan, repo)
    patched_src = next(iter(modified.values()))

    # Semantic: f-string SQL interpolation must be gone.
    assert 'f"SELECT' not in patched_src and "f'SELECT" not in patched_src
    assert "?" in patched_src or "%s" in patched_src

    if plan.test_spec is not None:
        suite_dir = tmp_path / "suite_cwe89"
        suite_dir.mkdir()
        _run_regression_suite(plan.test_spec, vulnerable_src, patched_src, suite_dir)


# ----------------------------------------------------------------------- CWE-502 deserialization


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

    vulnerable_src = (repo / "vulnerable.py").read_text(encoding="utf-8")
    modified = apply(plan, repo)
    patched_src = next(iter(modified.values()))

    # Semantic: pickle.loads on untrusted data must be gone.
    assert "pickle.loads(data)" not in patched_src

    if plan.test_spec is not None:
        suite_dir = tmp_path / "suite_cwe502"
        suite_dir.mkdir()
        _run_regression_suite(plan.test_spec, vulnerable_src, patched_src, suite_dir)
