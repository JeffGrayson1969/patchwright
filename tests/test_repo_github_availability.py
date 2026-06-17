"""GitHubRepoAdapter.is_available — `gh` presence + auth status, with caching."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from patchwright.adapters.repo_github import GitHubRepoAdapter


def _gh_ok() -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def _gh_fail(stderr: bytes = b"unauthenticated") -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout=b"", stderr=stderr)


def test_is_available_false_when_binary_missing() -> None:
    adapter = GitHubRepoAdapter()
    with patch("shutil.which", return_value=None):
        assert adapter.is_available() is False


def test_is_available_true_when_auth_status_succeeds() -> None:
    adapter = GitHubRepoAdapter()
    with (
        patch("shutil.which", return_value="/usr/local/bin/gh"),
        patch("subprocess.run", return_value=_gh_ok()),
    ):
        assert adapter.is_available() is True


def test_is_available_false_when_auth_status_fails() -> None:
    """gh auth status exits non-zero when not logged in."""
    adapter = GitHubRepoAdapter()
    with (
        patch("shutil.which", return_value="/usr/local/bin/gh"),
        patch("subprocess.run", return_value=_gh_fail()),
    ):
        assert adapter.is_available() is False


def test_is_available_caches_after_first_call() -> None:
    """Effect runner may call is_available across multiple transitions —
    re-shelling out to `gh auth status` every time would be wasteful."""
    adapter = GitHubRepoAdapter()
    with (
        patch("shutil.which", return_value="/usr/local/bin/gh") as which,
        patch("subprocess.run", return_value=_gh_ok()) as run,
    ):
        adapter.is_available()
        adapter.is_available()
        adapter.is_available()
    assert which.call_count == 1
    assert run.call_count == 1


def test_is_available_handles_timeout() -> None:
    adapter = GitHubRepoAdapter()
    with (
        patch("shutil.which", return_value="/usr/local/bin/gh"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh auth status", timeout=10),
        ),
    ):
        assert adapter.is_available() is False


def test_is_available_handles_oserror() -> None:
    """A broken `gh` install (permission denied, etc.) should not raise."""
    adapter = GitHubRepoAdapter()
    with (
        patch("shutil.which", return_value="/usr/local/bin/gh"),
        patch("subprocess.run", side_effect=OSError("permission denied")),
    ):
        assert adapter.is_available() is False


def test_custom_gh_binary_is_used() -> None:
    adapter = GitHubRepoAdapter(gh_binary="/opt/custom/gh")
    with (
        patch("shutil.which", return_value="/opt/custom/gh") as which,
        patch("subprocess.run", return_value=_gh_ok()) as run,
    ):
        adapter.is_available()
    which.assert_called_once_with("/opt/custom/gh")
    assert run.call_args[0][0][0] == "/opt/custom/gh"
