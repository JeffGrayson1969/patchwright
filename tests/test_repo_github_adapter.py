"""GitHubRepoAdapter — subprocess-mocked unit tests covering all 6 failure modes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from patchwright.adapters.repo_github import GitHubRepoAdapter
from patchwright.core.repo import RepoAdapter, RepoLocation

# --------------------------------------------------------------------------- helpers


def _ok(stdout: bytes = b"", stderr: bytes = b"") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: bytes = b"boom", returncode: int = 1) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=b"", stderr=stderr)


def _location(workspace: Path, host_repo: str = "owner/name") -> RepoLocation:
    return RepoLocation(workspace=workspace, host_repo=host_repo)


# --------------------------------------------------------------------------- Protocol conformance


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(GitHubRepoAdapter(), RepoAdapter)


def test_adapter_name_is_github() -> None:
    assert GitHubRepoAdapter().name == "github"


# --------------------------------------------------------------------------- create_branch


def test_create_branch_happy_path(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(stdout=b".git\n"),  # rev-parse --git-dir
            _ok(stdout=b""),  # status --porcelain (clean)
            _ok(stdout=b"abcdef0123\n"),  # rev-parse base
            _ok(),  # switch -c
        ]
        result = adapter.create_branch(
            location=_location(tmp_path),
            branch="patchwright/case-abc123",
        )
    assert result.ok is True
    assert result.branch == "patchwright/case-abc123"
    assert result.base_sha == "abcdef0123"
    assert result.reason == ""
    switch_argv = run.call_args_list[3][0][0]
    assert switch_argv[1:4] == ["switch", "-c", "patchwright/case-abc123"]


def test_create_branch_fails_when_workspace_missing(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    missing = tmp_path / "nope"
    result = adapter.create_branch(location=_location(missing), branch="b")
    assert result.ok is False
    assert "workspace does not exist" in result.reason


def test_create_branch_fails_when_not_a_git_repo(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.return_value = _fail(stderr=b"not a git repository")
        result = adapter.create_branch(location=_location(tmp_path), branch="b")
    assert result.ok is False
    assert "not a git repository" in result.reason


def test_create_branch_fails_on_dirty_tree(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(stdout=b".git\n"),  # rev-parse --git-dir
            _ok(stdout=b" M file.py\n"),  # dirty
        ]
        result = adapter.create_branch(location=_location(tmp_path), branch="b")
    assert result.ok is False
    assert "uncommitted changes" in result.reason


def test_create_branch_fails_on_invalid_base_ref(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(stdout=b".git\n"),
            _ok(stdout=b""),
            _fail(stderr=b"unknown revision: nope"),
        ]
        result = adapter.create_branch(location=_location(tmp_path), branch="b", base="nope")
    assert result.ok is False
    assert "'nope' not found" in result.reason


def test_create_branch_fails_when_branch_already_exists(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(stdout=b".git\n"),
            _ok(stdout=b""),
            _ok(stdout=b"abcdef\n"),
            _fail(stderr=b"fatal: a branch named 'b' already exists"),
        ]
        result = adapter.create_branch(location=_location(tmp_path), branch="b")
    assert result.ok is False
    assert "branch already exists: b" in result.reason


# --------------------------------------------------------------------------- commit_files


def test_commit_files_happy_path(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    files = {tmp_path / "src" / "x.py": "print('x')\n"}

    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(stdout=b"patchwright/case-abc\n"),  # rev-parse --abbrev-ref HEAD
            _ok(),  # git add
            _ok(),  # git commit
            _ok(stdout=b"cafef00d\n"),  # rev-parse HEAD
        ]
        result = adapter.commit_files(
            location=_location(tmp_path),
            branch="patchwright/case-abc",
            files=files,
            message="feat(test): add x",
            author_name="Test User",
            author_email="test@example.com",
        )
    assert result.ok is True
    assert result.commit_sha == "cafef00d"
    assert result.committed_paths == ("src/x.py",)
    # File actually written
    assert (tmp_path / "src" / "x.py").read_text() == "print('x')\n"
    # git add invoked with explicit paths, never -A
    add_argv = run.call_args_list[1][0][0]
    assert "-A" not in add_argv
    assert "--" in add_argv
    # git commit uses operator identity passed in, never from ambient env
    commit_argv = run.call_args_list[2][0][0]
    assert "user.name=Test User" in commit_argv
    assert "user.email=test@example.com" in commit_argv


def test_commit_files_empty_dict_rejected(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    result = adapter.commit_files(
        location=_location(tmp_path),
        branch="b",
        files={},
        message="m",
        author_name="n",
        author_email="e@x",
    )
    assert result.ok is False
    assert result.reason == "no files to commit"


def test_commit_files_fails_when_head_not_on_branch(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.return_value = _ok(stdout=b"main\n")
        result = adapter.commit_files(
            location=_location(tmp_path),
            branch="patchwright/case-abc",
            files={tmp_path / "x.py": "x"},
            message="m",
            author_name="n",
            author_email="e@x",
        )
    assert result.ok is False
    assert "HEAD is not on 'patchwright/case-abc'" in result.reason


def test_commit_files_fails_when_path_outside_workspace(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    outside = tmp_path.parent / "elsewhere.py"
    with patch("subprocess.run") as run:
        run.return_value = _ok(stdout=b"b\n")
        result = adapter.commit_files(
            location=_location(tmp_path),
            branch="b",
            files={outside: "x"},
            message="m",
            author_name="n",
            author_email="e@x",
        )
    assert result.ok is False
    assert "outside workspace" in result.reason


def test_commit_files_fails_when_git_add_fails(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(stdout=b"b\n"),
            _fail(stderr=b"fatal: pathspec did not match"),
        ]
        result = adapter.commit_files(
            location=_location(tmp_path),
            branch="b",
            files={tmp_path / "x.py": "x"},
            message="m",
            author_name="n",
            author_email="e@x",
        )
    assert result.ok is False
    assert "git add failed" in result.reason


def test_commit_files_fails_when_git_commit_fails(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(stdout=b"b\n"),
            _ok(),
            _fail(stderr=b"nothing to commit"),
        ]
        result = adapter.commit_files(
            location=_location(tmp_path),
            branch="b",
            files={tmp_path / "x.py": "x"},
            message="m",
            author_name="n",
            author_email="e@x",
        )
    assert result.ok is False
    assert "git commit failed" in result.reason


def test_commit_files_writes_nested_directories(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    nested = tmp_path / "src" / "deep" / "nested" / "file.py"
    with patch("subprocess.run") as run:
        run.side_effect = [_ok(stdout=b"b\n"), _ok(), _ok(), _ok(stdout=b"sha\n")]
        result = adapter.commit_files(
            location=_location(tmp_path),
            branch="b",
            files={nested: "content"},
            message="m",
            author_name="n",
            author_email="e@x",
        )
    assert result.ok is True
    assert nested.exists()


# --------------------------------------------------------------------------- open_pr_draft


def test_open_pr_draft_happy_path(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [
            _ok(),  # git push
            _ok(stdout=b"https://github.com/owner/name/pull/42\n"),  # gh pr create
        ]
        result = adapter.open_pr_draft(
            location=_location(tmp_path),
            branch="patchwright/case-abc",
            base_branch="main",
            title="fix: CWE-22",
            body="evidence packet",
        )
    assert result.ok is True
    assert result.pr_number == 42
    assert result.pr_url == "https://github.com/owner/name/pull/42"
    assert result.base_branch == "main"

    push_argv = run.call_args_list[0][0][0]
    assert push_argv[1:5] == ["push", "-u", "origin", "patchwright/case-abc"]

    gh_argv = run.call_args_list[1][0][0]
    assert gh_argv[1:3] == ["pr", "create"]
    assert "--draft" in gh_argv
    assert "--repo" in gh_argv
    assert "owner/name" in gh_argv


def test_open_pr_draft_always_passes_draft_flag(tmp_path: Path) -> None:
    """CLAUDE.md #8: --draft is hardcoded. Belt-and-suspenders verification
    that no code path produces a non-draft PR."""
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [_ok(), _ok(stdout=b"https://github.com/x/y/pull/1\n")]
        adapter.open_pr_draft(
            location=_location(tmp_path),
            branch="b",
            base_branch="main",
            title="t",
            body="b",
        )
    gh_argv = run.call_args_list[1][0][0]
    assert "--draft" in gh_argv
    for forbidden in ("--admin", "--merge", "--auto", "--enable-auto-merge", "--squash"):
        assert forbidden not in gh_argv, (
            f"open_pr_draft must not pass {forbidden!r} — violates CLAUDE.md #8"
        )


@pytest.mark.parametrize(
    ("pr_url", "expected_number"),
    [
        ("https://github.com/owner/name/pull/1", 1),
        ("https://github.com/o/n/pull/9999\n", 9999),
        ("https://example.com/repo/pull/42#issue-comment", 42),
        ("not a real url", None),
    ],
)
def test_open_pr_draft_parses_pr_number_from_stdout(
    tmp_path: Path, pr_url: str, expected_number: int | None
) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [_ok(), _ok(stdout=pr_url.encode())]
        result = adapter.open_pr_draft(
            location=_location(tmp_path),
            branch="b",
            base_branch="main",
            title="t",
            body="b",
        )
    assert result.ok is True
    assert result.pr_number == expected_number


def test_open_pr_draft_fails_on_push_rejection(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [_fail(stderr=b"remote rejected")]
        result = adapter.open_pr_draft(
            location=_location(tmp_path),
            branch="b",
            base_branch="main",
            title="t",
            body="b",
        )
    assert result.ok is False
    assert "push rejected" in result.reason
    assert result.pr_number is None
    assert result.pr_url is None


def test_open_pr_draft_fails_on_gh_create_failure(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [_ok(), _fail(stderr=b"GraphQL: rate limited")]
        result = adapter.open_pr_draft(
            location=_location(tmp_path),
            branch="b",
            base_branch="main",
            title="t",
            body="b",
        )
    assert result.ok is False
    assert "gh pr create failed" in result.reason


def test_open_pr_draft_handles_empty_stdout(tmp_path: Path) -> None:
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [_ok(), _ok(stdout=b"")]
        result = adapter.open_pr_draft(
            location=_location(tmp_path),
            branch="b",
            base_branch="main",
            title="t",
            body="b",
        )
    assert result.ok is False
    assert "no URL" in result.reason


# ------------------------------------------------------------- security: no GH_TOKEN passed


def test_adapter_never_sets_gh_token_in_subprocess_env(tmp_path: Path) -> None:
    """NFR-S-10: PatchWright never reads or sets GH_TOKEN. The adapter
    inherits the operator's keyring-backed `gh auth` session."""
    adapter = GitHubRepoAdapter()
    with patch("subprocess.run") as run:
        run.side_effect = [_ok(), _ok(stdout=b"https://x/pull/1\n")]
        adapter.open_pr_draft(
            location=_location(tmp_path),
            branch="b",
            base_branch="main",
            title="t",
            body="b",
        )
    # If the adapter ever passes env= it should NOT include GH_TOKEN. Subprocess
    # called without env= inherits the parent's environment, which is fine —
    # the operator's `gh auth` token isn't in env, it's in their keyring.
    for call in run.call_args_list:
        passed_env = call.kwargs.get("env")
        if passed_env is not None:
            assert "GH_TOKEN" not in passed_env
            assert "GITHUB_TOKEN" not in passed_env
