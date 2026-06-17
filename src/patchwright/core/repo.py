"""RepoAdapter Protocol — the boundary between PatchWright and git hosts.

The post-transition effect runner (M2-pr.5) calls into this Protocol to
turn a PatchApplyResult into a feature branch + draft PR. The GitHub
backend (adapters/repo_github.py, M2-pr.2) is the only impl in P1; GitLab
/ Bitbucket / Gerrit land in P2+ behind the same shape.

PRD §6.4 / FR-PT-3 commitment: every patch lands as a draft PR with the
operator as the author. PRD §10.1 commitment #8 ("no auto-merge in v1")
is enforced structurally here — this Protocol has no merge_pr / auto_merge
method. Adding one is a deliberate PRD revision, not a casual addition.

Design rules:
- Adapters do disk + network I/O. They are NOT agents (CLAUDE.md #3) and
  must never appear in patchwright.agents.*. The orchestrator's
  post-transition effect runner is the only legitimate caller in v1.
- No method raises on expected failure (auth missing, dirty tree, branch
  exists). Result objects carry ok + reason so journal entries stay
  uniform and the FSM never sees an exception escape into a transition.
- Operator identity is passed in by the effect runner — adapters do not
  read ambient env or `git config` themselves, so they stay testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from patchwright.core.config import PatchwrightConfig


class RepoConfigError(Exception):
    """Raised when the repo adapter cannot be built (unknown adapter, missing
    impl). Per-call failures are returned as result objects, not raised."""


# --------------------------------------------------------------------------- value types


@dataclass(frozen=True)
class RepoLocation:
    """Logical coordinates for the repo being modified.

    Concrete adapters interpret host_repo. For GitHub it is "owner/name".
    workspace is the local checkout the codemod modified; it must already
    exist and be a git repo (the adapter validates this on every call).
    """

    workspace: Path
    host_repo: str


# --------------------------------------------------------------------------- result types


class CreateBranchResult(BaseModel):
    """Outcome of `create_branch`. Returned (not raised) for every terminal
    condition the adapter can distinguish — branch already exists, dirty
    tree, workspace not a git repo, etc."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    branch: str
    base_sha: str
    """SHA the branch was cut from. Empty string when ok=False."""

    reason: str = ""
    """Populated when ok=False. Single line, suitable for journal entry."""


class CommitFilesResult(BaseModel):
    """Outcome of `commit_files`. The adapter writes the files dict to disk
    and stages + commits them in one operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    branch: str
    commit_sha: str
    """SHA of the new commit. Empty string when ok=False."""

    committed_paths: tuple[str, ...]
    """Repo-relative POSIX paths that were committed. Empty when ok=False."""

    reason: str = ""


class OpenPRDraftResult(BaseModel):
    """Outcome of `open_pr_draft`. The PR is ALWAYS opened in draft state —
    CLAUDE.md #8. There is no non-draft variant on this Protocol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    pr_number: int | None
    pr_url: str | None
    branch: str
    base_branch: str
    """What the PR is opened against (typically 'main'). Recorded so the
    journal entry stays self-describing when an operator changes defaults."""

    reason: str = ""


# --------------------------------------------------------------------------- Protocol


@runtime_checkable
class RepoAdapter(Protocol):
    """A backend that can create a feature branch, commit files, and open
    a draft PR on a git host.

    Implementations:
      - adapters/repo_github.py    (M2-pr.2 — `gh` subprocess)
      - adapters/repo_gitlab.py    (P2+ — not yet planned)

    Structural enforcement of CLAUDE.md #8: no `merge_pr` method, no
    `enable_auto_merge` method, no `mark_ready_for_review` method. The
    type system refuses to compile a backend that auto-files a non-draft.
    """

    name: str
    """Stable identifier — recorded in journal entries (branch_created,
    commit_pushed, pr_drafted, pr_draft_failed)."""

    def is_available(self) -> bool:
        """True iff the backend can actually be invoked on this host (e.g.
        `gh` on PATH AND authenticated). Callers use this to journal a
        clean pr_draft_failed instead of propagating a subprocess error."""
        ...

    def create_branch(
        self,
        *,
        location: RepoLocation,
        branch: str,
        base: str = "HEAD",
    ) -> CreateBranchResult: ...

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
        """Write `files` (absolute path -> new content) into the workspace,
        stage exactly those paths, and create one commit on `branch`.

        `files` matches the return type of tools.codemod_python.apply() so
        the post-transition effect runner does zero translation between
        the codemod output and the adapter input.
        """
        ...

    def open_pr_draft(
        self,
        *,
        location: RepoLocation,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> OpenPRDraftResult:
        """Push `branch` and open a DRAFT PR against `base_branch`. The
        PR title is typically the PatchPlan summary; body is the rendered
        evidence packet from core/evidence.py."""
        ...


# --------------------------------------------------------------------------- factory


def default_repo_adapter(config: PatchwrightConfig) -> RepoAdapter:
    """Instantiate the configured RepoAdapter.

    Raises:
        RepoConfigError: when the configured adapter has no registered
            implementation. The github backend lands in AEG-422 (M2-pr.2);
            until then this raises for every config.
    """
    if config.repo.adapter == "github":
        try:
            from patchwright.adapters.repo_github import GitHubRepoAdapter  # noqa: PLC0415
        except ImportError as exc:
            raise RepoConfigError(
                "github repo adapter not yet implemented — lands in AEG-422 (M2-pr.2). "
                "Track: https://linear.app/aegisq/issue/AEG-422"
            ) from exc
        return GitHubRepoAdapter()

    raise RepoConfigError(f"unknown repo.adapter: {config.repo.adapter!r}")  # pragma: no cover


__all__ = [
    "CommitFilesResult",
    "CreateBranchResult",
    "OpenPRDraftResult",
    "RepoAdapter",
    "RepoConfigError",
    "RepoLocation",
    "default_repo_adapter",
]
