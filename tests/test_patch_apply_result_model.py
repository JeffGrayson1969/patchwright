"""PatchApplyResult + TestResult schema sanity (AEG-424)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from patchwright.core.hashing import canonical_json
from patchwright.models.patch_apply_result import PatchApplyResult, TestResult


def _ok_test_result() -> TestResult:
    return TestResult(exit_code=0, stdout_tail="ok\n", stderr_tail="", timed_out=False)


def _ok_result() -> PatchApplyResult:
    return PatchApplyResult(
        case_id="case-abc123def456",
        plan_artifact_id="sha256:" + "a" * 64,
        modified_files=("src/x.py", "tests/test_x.py"),
        diff="--- a/src/x.py\n+++ b/src/x.py\n@@ +1 @@\n+x\n",
        test_result=_ok_test_result(),
        scratch_dir="/tmp/case/scratch/case-abc/worktree",
        branch_name="patchwright/case-abc123def456",
        base_branch="main",
        commit_message="fix: bar\n\nrationale",
    )


# --------------------------------------------------------------------------- TestResult


def test_test_result_round_trip() -> None:
    r = _ok_test_result()
    assert TestResult.model_validate_json(r.model_dump_json()) == r


def test_test_result_is_frozen() -> None:
    r = _ok_test_result()
    with pytest.raises(ValidationError):
        r.exit_code = 1  # type: ignore[misc]


def test_test_result_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        TestResult.model_validate({"exit_code": 0, "extra": True})


def test_test_result_stdout_tail_bounded() -> None:
    with pytest.raises(ValidationError):
        TestResult(exit_code=0, stdout_tail="x" * 5000)


def test_test_result_stderr_tail_bounded() -> None:
    with pytest.raises(ValidationError):
        TestResult(exit_code=0, stderr_tail="x" * 5000)


def test_test_result_defaults() -> None:
    r = TestResult(exit_code=0)
    assert r.stdout_tail == ""
    assert r.stderr_tail == ""
    assert r.timed_out is False


# --------------------------------------------------------------------------- PatchApplyResult


def test_patch_apply_result_round_trip() -> None:
    r = _ok_result()
    assert PatchApplyResult.model_validate_json(r.model_dump_json()) == r


def test_patch_apply_result_is_frozen() -> None:
    r = _ok_result()
    with pytest.raises(ValidationError):
        r.case_id = "other"  # type: ignore[misc]


def test_patch_apply_result_extra_forbidden() -> None:
    bad = _ok_result().model_dump(mode="json")
    bad["unknown"] = "x"
    with pytest.raises(ValidationError):
        PatchApplyResult.model_validate(bad)


def test_patch_apply_result_schema_version_pinned() -> None:
    """schema_version must be '1' — bumping is a breaking change for any
    consumer (effect runner, evidence pack, journal replay)."""
    bad = _ok_result().model_dump(mode="json")
    bad["schema_version"] = "2"
    with pytest.raises(ValidationError):
        PatchApplyResult.model_validate(bad)


def test_patch_apply_result_canonical_json_is_deterministic() -> None:
    r = _ok_result()
    a = canonical_json(r.model_dump(mode="json"))
    b = canonical_json(r.model_dump(mode="json"))
    assert a == b


def test_modified_files_is_a_tuple() -> None:
    """tuple not list — pydantic-frozen equality + hashability for tests."""
    r = _ok_result()
    assert isinstance(r.modified_files, tuple)
