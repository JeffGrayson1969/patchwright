"""End-to-end fixture corpus tests (FR-PT-1 Phase B exit criterion).

For each CWE fixture under tests/fixtures/patch_corpus/, load the plan,
apply it to a copy of vulnerable.py in a temp dir, and assert:
  1. The patched source matches the expected.py fixture file byte-for-byte.
  2. apply() is idempotent — running it again on the patched repo is a no-op.
  3. diff() produces a non-empty unified diff containing the file path.

Test corpus is intentionally small (3 CWE shapes) — per the plan, breadth
across CWE classes comes via real-world reports in M9, not here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from patchwright.models.patch_plan import PatchPlan
from patchwright.tools.codemod_python import apply, diff, write_modified

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "patch_corpus"

FIXTURE_DIRS = sorted(p for p in FIXTURE_ROOT.iterdir() if p.is_dir())


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)
def test_fixture_apply_matches_expected(tmp_path: Path, fixture_dir: Path) -> None:
    # Copy vulnerable.py into a fresh "repo" so we can compare.
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(fixture_dir / "vulnerable.py", repo / "vulnerable.py")

    plan = PatchPlan.model_validate_json((fixture_dir / "plan.json").read_text())
    modified = apply(plan, repo)

    patched_source = modified[(repo / "vulnerable.py").resolve()]
    expected_path = fixture_dir / "expected.py"
    if not expected_path.exists():
        # First-run convenience: write the actual output so a human can review +
        # commit it as the expected fixture. After commit, the assert below
        # locks the behavior.
        expected_path.write_text(patched_source, encoding="utf-8")
        pytest.fail(
            f"created {expected_path.name} from codemod output — review the diff and re-run"
        )
    assert patched_source == expected_path.read_text()


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)
def test_fixture_apply_is_idempotent(tmp_path: Path, fixture_dir: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(fixture_dir / "vulnerable.py", repo / "vulnerable.py")

    plan = PatchPlan.model_validate_json((fixture_dir / "plan.json").read_text())
    first = apply(plan, repo)
    write_modified(first)

    # Run a SECOND apply against the now-patched repo. ReplaceFunctionBody
    # would re-replace with identical bytes, InsertImport is no-op, etc.
    second = apply(plan, repo)
    for path, source in second.items():
        assert source == first[path], f"non-idempotent at {path.name}"


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)
def test_fixture_diff_is_non_empty(tmp_path: Path, fixture_dir: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(fixture_dir / "vulnerable.py", repo / "vulnerable.py")

    plan = PatchPlan.model_validate_json((fixture_dir / "plan.json").read_text())
    modified = apply(plan, repo)
    d = diff(repo, modified)
    assert "vulnerable.py" in d
    assert d.startswith("---") or "@@" in d


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)
def test_fixture_diff_matches_expected_byte_for_byte(tmp_path: Path, fixture_dir: Path) -> None:
    """M2-codemod exit criterion (phase1-work-plan.md): apply(plan, repo) ->
    diff produces the expected diff byte-for-byte."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(fixture_dir / "vulnerable.py", repo / "vulnerable.py")

    plan = PatchPlan.model_validate_json((fixture_dir / "plan.json").read_text())
    modified = apply(plan, repo)
    actual_diff = diff(repo, modified)

    expected_diff_path = fixture_dir / "expected.diff"
    assert expected_diff_path.exists(), f"missing expected.diff in {fixture_dir.name}"
    assert actual_diff == expected_diff_path.read_text()


def test_every_fixture_has_required_files() -> None:
    """Guard: every fixture dir needs vulnerable.py + plan.json. expected.py is
    auto-created on first run by the test above."""
    assert len(FIXTURE_DIRS) >= 3, f"expected ≥3 fixtures, found {len(FIXTURE_DIRS)}"
    for fd in FIXTURE_DIRS:
        assert (fd / "vulnerable.py").exists(), f"missing vulnerable.py in {fd.name}"
        assert (fd / "plan.json").exists(), f"missing plan.json in {fd.name}"
        # Validate plan.json parses as a PatchPlan
        PatchPlan.model_validate_json((fd / "plan.json").read_text())


def test_fixture_plan_summaries_are_unique() -> None:
    summaries = []
    for fd in FIXTURE_DIRS:
        plan = json.loads((fd / "plan.json").read_text())
        summaries.append(plan["summary"])
    assert len(summaries) == len(set(summaries))
