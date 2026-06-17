"""RepoAdapter Protocol + result-type shapes + RepoConfig (AEG-421, FR-PT-3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from patchwright.core.config import PatchwrightConfig, RepoConfig
from patchwright.core.repo import (
    CommitFilesResult,
    CreateBranchResult,
    OpenPRDraftResult,
    RepoAdapter,
    RepoConfigError,
    RepoLocation,
    default_repo_adapter,
)

# --------------------------------------------------------------------------- result types


def test_create_branch_result_round_trip() -> None:
    r = CreateBranchResult(ok=True, branch="patchwright/case-abc123", base_sha="deadbeef")
    assert CreateBranchResult.model_validate_json(r.model_dump_json()) == r


def test_create_branch_result_is_frozen() -> None:
    r = CreateBranchResult(ok=True, branch="x", base_sha="y")
    with pytest.raises(ValidationError):
        r.ok = False  # type: ignore[misc]


def test_create_branch_result_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        CreateBranchResult.model_validate(
            {"ok": True, "branch": "x", "base_sha": "y", "extra": 1}
        )


def test_create_branch_result_reason_defaults_empty() -> None:
    r = CreateBranchResult(ok=False, branch="x", base_sha="")
    assert r.reason == ""


def test_commit_files_result_round_trip() -> None:
    r = CommitFilesResult(
        ok=True,
        branch="patchwright/case-abc",
        commit_sha="cafe",
        committed_paths=("src/x.py", "tests/test_x.py"),
    )
    assert CommitFilesResult.model_validate_json(r.model_dump_json()) == r


def test_commit_files_result_is_frozen() -> None:
    r = CommitFilesResult(ok=True, branch="x", commit_sha="y", committed_paths=())
    with pytest.raises(ValidationError):
        r.commit_sha = "z"  # type: ignore[misc]


def test_commit_files_result_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        CommitFilesResult.model_validate(
            {
                "ok": True,
                "branch": "x",
                "commit_sha": "y",
                "committed_paths": [],
                "extra": True,
            }
        )


def test_open_pr_draft_result_round_trip() -> None:
    r = OpenPRDraftResult(
        ok=True,
        pr_number=42,
        pr_url="https://example.com/owner/repo/pull/42",
        branch="patchwright/case-abc",
        base_branch="main",
    )
    assert OpenPRDraftResult.model_validate_json(r.model_dump_json()) == r


def test_open_pr_draft_result_allows_null_pr_when_failed() -> None:
    r = OpenPRDraftResult(
        ok=False,
        pr_number=None,
        pr_url=None,
        branch="patchwright/case-abc",
        base_branch="main",
        reason="gh pr create failed: rate limited",
    )
    assert r.pr_number is None
    assert r.pr_url is None
    assert r.reason


def test_open_pr_draft_result_is_frozen() -> None:
    r = OpenPRDraftResult(
        ok=True, pr_number=1, pr_url="http://x", branch="b", base_branch="main"
    )
    with pytest.raises(ValidationError):
        r.pr_number = 2  # type: ignore[misc]


# --------------------------------------------------------------------------- RepoLocation


def test_repo_location_is_frozen(tmp_path: Path) -> None:
    loc = RepoLocation(workspace=tmp_path, host_repo="owner/name")
    with pytest.raises((AttributeError, TypeError)):
        loc.host_repo = "other/name"  # type: ignore[misc]


def test_repo_location_equality(tmp_path: Path) -> None:
    a = RepoLocation(workspace=tmp_path, host_repo="owner/name")
    b = RepoLocation(workspace=tmp_path, host_repo="owner/name")
    assert a == b
    assert hash(a) == hash(b)


# --------------------------------------------------------------------------- Protocol


def _make_stub_adapter() -> object:
    """A minimal structural match for RepoAdapter. Used to assert the Protocol
    actually accepts a clean impl + rejects holes."""

    class _Stub:
        name = "stub"

        def is_available(self) -> bool:
            return True

        def create_branch(
            self,
            *,
            location: RepoLocation,
            branch: str,
            base: str = "HEAD",
        ) -> CreateBranchResult:
            return CreateBranchResult(ok=True, branch=branch, base_sha="abc")

        def commit_files(
            self,
            *,
            location: RepoLocation,
            branch: str,
            files: dict[Path, str],
            message: str,
            author_name: str,
            author_email: str,
        ) -> CommitFilesResult:
            return CommitFilesResult(
                ok=True, branch=branch, commit_sha="def", committed_paths=()
            )

        def open_pr_draft(
            self,
            *,
            location: RepoLocation,
            branch: str,
            base_branch: str,
            title: str,
            body: str,
        ) -> OpenPRDraftResult:
            return OpenPRDraftResult(
                ok=True,
                pr_number=1,
                pr_url="http://example/1",
                branch=branch,
                base_branch=base_branch,
            )

    return _Stub()


def test_full_stub_satisfies_protocol() -> None:
    assert isinstance(_make_stub_adapter(), RepoAdapter)


def test_partial_stub_fails_protocol_check() -> None:
    class _Partial:
        name = "partial"

        def is_available(self) -> bool:
            return True

    assert not isinstance(_Partial(), RepoAdapter)


# ------------------------------------------------------------ CLAUDE.md #8 structural enforcement


@pytest.mark.parametrize(
    "forbidden",
    ["merge_pr", "enable_auto_merge", "auto_merge", "mark_ready_for_review"],
)
def test_repo_adapter_protocol_has_no_merge_surface(forbidden: str) -> None:
    """CLAUDE.md non-negotiable #8: no auto-merge, no auto-file. The Protocol
    structurally refuses to compile a backend that auto-files a non-draft."""
    assert not hasattr(RepoAdapter, forbidden), (
        f"RepoAdapter exposes forbidden method '{forbidden}' — violates CLAUDE.md #8"
    )


# --------------------------------------------------------------------------- factory


def test_default_repo_adapter_raises_until_github_lands() -> None:
    """Exit criterion for AEG-421: factory raises a clear error when no impl
    is registered yet. AEG-422 lands the github impl and this test should
    then assert the returned object is a RepoAdapter."""
    with pytest.raises(RepoConfigError, match="AEG-422"):
        default_repo_adapter(PatchwrightConfig())


# --------------------------------------------------------------------------- RepoConfig


def test_repo_config_defaults() -> None:
    c = RepoConfig()
    assert c.adapter == "github"
    assert c.default_base_branch == "main"


def test_repo_config_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        RepoConfig.model_validate({"adapter": "github", "unknown": True})


def test_repo_config_rejects_unknown_adapter() -> None:
    with pytest.raises(ValidationError):
        RepoConfig.model_validate({"adapter": "gitlab"})


def test_patchwright_config_includes_repo_section() -> None:
    c = PatchwrightConfig()
    assert c.repo.adapter == "github"
    assert c.repo.default_base_branch == "main"


def test_patchwright_config_repo_overrides_load_from_yaml() -> None:
    raw = {"repo": {"default_base_branch": "develop"}}
    c = PatchwrightConfig.model_validate(raw)
    assert c.repo.adapter == "github"
    assert c.repo.default_base_branch == "develop"
