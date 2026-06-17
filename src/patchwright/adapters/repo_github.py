"""GitHubRepoAdapter — `gh` + `git` subprocess wrapper.

Why subprocess instead of PyGithub (open question §1 resolved in plan):
  - `gh` inherits the operator's existing auth (NFR-S-10 — no PAT in
    keyring, no plaintext token in config).
  - The CLI is the universal GitHub interface; ergonomics are fine.
  - Trivial to mock in tests (patch `subprocess.run`).
  - Operators already have `gh` installed for everyday workflow.

CLAUDE.md #8 structural enforcement: this adapter has no merge_pr,
no enable_auto_merge, no mark_ready_for_review method. Adding one is
a deliberate PRD revision, not a casual addition. `--draft` is hardcoded
on every PR-create call.

All expected failures (auth missing, dirty tree, branch exists, push
rejected, gh-cli failure) return a result with ok=False and a structured
reason — never raise. The effect runner journals the reason and leaves
the FSM state unchanged.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from patchwright.core.repo import (
    CommitFilesResult,
    CreateBranchResult,
    OpenPRDraftResult,
    RepoLocation,
)

_MAX_OUTPUT_BYTES = 256 * 1024  # 256 KiB — per plan §2
_DEFAULT_TIMEOUT = 30.0
_AVAILABILITY_TIMEOUT = 10.0

_PR_URL_RE = re.compile(r"/pull/(\d+)(?:[#?].*)?$")


@dataclass(frozen=True)
class _SubprocResult:
    """Internal result of a single subprocess invocation.

    Never raised — every shape (success, non-zero, timeout, binary not
    found) becomes a value, so the adapter methods can route on it
    uniformly.
    """

    returncode: int
    stdout: str
    stderr: str
    truncated: bool


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    max_bytes: int = _MAX_OUTPUT_BYTES,
) -> _SubprocResult:
    """Run cmd as a subprocess. Returns a _SubprocResult for every terminal
    condition the subprocess can produce — does not raise."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return _SubprocResult(
            returncode=127, stdout="", stderr=f"binary not found: {cmd[0]}", truncated=False
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return _SubprocResult(returncode=-1, stdout="", stderr=str(exc), truncated=False)

    out_bytes = proc.stdout or b""
    err_bytes = proc.stderr or b""
    truncated = len(out_bytes) > max_bytes or len(err_bytes) > max_bytes
    return _SubprocResult(
        returncode=proc.returncode,
        stdout=out_bytes[:max_bytes].decode("utf-8", errors="replace"),
        stderr=err_bytes[:max_bytes].decode("utf-8", errors="replace"),
        truncated=truncated,
    )


def _first_line(text: str) -> str:
    """Single-line summary for the `reason` field of result objects."""
    return text.splitlines()[0] if text else ""


@dataclass
class GitHubRepoAdapter:
    """RepoAdapter backed by the `gh` and `git` CLIs.

    Inherits the operator's `gh auth` session; never reads or sets
    GH_TOKEN itself (NFR-S-10). Per-call failures are returned as result
    objects, not raised.
    """

    name: str = "github"
    gh_binary: str = "gh"
    git_binary: str = "git"
    max_output_bytes: int = _MAX_OUTPUT_BYTES
    default_timeout: float = _DEFAULT_TIMEOUT

    _availability_cache: bool | None = field(default=None, init=False, repr=False)

    # ----------------------------------------------------------------- availability

    def is_available(self) -> bool:
        """True iff `gh` is on PATH AND `gh auth status` reports an
        authenticated session. Cached after the first call so the effect
        runner can re-check cheaply on subsequent transitions."""
        if self._availability_cache is not None:
            return self._availability_cache

        if shutil.which(self.gh_binary) is None:
            self._availability_cache = False
            return False

        auth = _run(
            [self.gh_binary, "auth", "status"],
            timeout=_AVAILABILITY_TIMEOUT,
            max_bytes=self.max_output_bytes,
        )
        self._availability_cache = auth.returncode == 0
        return self._availability_cache

    # ----------------------------------------------------------------- create_branch

    def create_branch(  # noqa: PLR0911 — one early-return per failure mode is the contract
        self,
        *,
        location: RepoLocation,
        branch: str,
        base: str = "HEAD",
    ) -> CreateBranchResult:
        if not location.workspace.is_dir():
            return CreateBranchResult(
                ok=False,
                branch=branch,
                base_sha="",
                reason=f"workspace does not exist: {location.workspace}",
            )

        # 1. workspace must be a git repo
        check = self._git(["rev-parse", "--git-dir"], cwd=location.workspace)
        if check.returncode != 0:
            return CreateBranchResult(
                ok=False,
                branch=branch,
                base_sha="",
                reason=f"not a git repository: {location.workspace}",
            )

        # 2. working tree must be clean — otherwise a new branch would carry stray edits
        status = self._git(["status", "--porcelain"], cwd=location.workspace)
        if status.returncode != 0:
            return CreateBranchResult(
                ok=False,
                branch=branch,
                base_sha="",
                reason=f"git status failed: {_first_line(status.stderr)}",
            )
        if status.stdout.strip():
            return CreateBranchResult(
                ok=False,
                branch=branch,
                base_sha="",
                reason="workspace has uncommitted changes; run on a clean tree",
            )

        # 3. resolve base SHA so the journal entry records exactly what we cut from
        rev = self._git(["rev-parse", base], cwd=location.workspace)
        if rev.returncode != 0:
            return CreateBranchResult(
                ok=False,
                branch=branch,
                base_sha="",
                reason=f"base ref {base!r} not found: {_first_line(rev.stderr)}",
            )
        base_sha = rev.stdout.strip()

        # 4. cut the new branch
        switch = self._git(["switch", "-c", branch, base_sha], cwd=location.workspace)
        if switch.returncode != 0:
            err = switch.stderr.strip()
            if "already exists" in err.lower():
                return CreateBranchResult(
                    ok=False,
                    branch=branch,
                    base_sha=base_sha,
                    reason=f"branch already exists: {branch}",
                )
            return CreateBranchResult(
                ok=False,
                branch=branch,
                base_sha=base_sha,
                reason=f"git switch failed: {_first_line(err)}",
            )

        return CreateBranchResult(ok=True, branch=branch, base_sha=base_sha)

    # ----------------------------------------------------------------- commit_files

    def commit_files(  # noqa: PLR0911 — one early-return per failure mode is the contract
        self,
        *,
        location: RepoLocation,
        branch: str,
        files: dict[Path, str],
        message: str,
        author_name: str,
        author_email: str,
    ) -> CommitFilesResult:
        if not files:
            return CommitFilesResult(
                ok=False,
                branch=branch,
                commit_sha="",
                committed_paths=(),
                reason="no files to commit",
            )

        # 1. HEAD must be on the target branch — otherwise we'd commit on whatever the
        #    caller switched to last, which is a class of bug we want to fail loudly on
        head = self._git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=location.workspace)
        current = head.stdout.strip()
        if head.returncode != 0 or current != branch:
            return CommitFilesResult(
                ok=False,
                branch=branch,
                commit_sha="",
                committed_paths=(),
                reason=f"HEAD is not on {branch!r}: {current or 'unknown'}",
            )

        # 2. write files to disk + collect repo-relative paths for `git add`
        ws_resolved = location.workspace.resolve()
        committed_paths: list[str] = []
        for path, content in sorted(files.items(), key=lambda kv: str(kv[0])):
            abs_path = path if path.is_absolute() else (location.workspace / path)
            abs_resolved = abs_path.resolve(strict=False)
            try:
                rel = abs_resolved.relative_to(ws_resolved)
            except ValueError:
                return CommitFilesResult(
                    ok=False,
                    branch=branch,
                    commit_sha="",
                    committed_paths=(),
                    reason=f"file path outside workspace: {path}",
                )
            abs_resolved.parent.mkdir(parents=True, exist_ok=True)
            abs_resolved.write_text(content, encoding="utf-8")
            committed_paths.append(rel.as_posix())

        # 3. stage exactly those paths — never `git add -A` (CLAUDE.md commit safety)
        add = self._git(["add", "--", *committed_paths], cwd=location.workspace)
        if add.returncode != 0:
            return CommitFilesResult(
                ok=False,
                branch=branch,
                commit_sha="",
                committed_paths=(),
                reason=f"git add failed: {_first_line(add.stderr)}",
            )

        # 4. commit with the operator's identity (passed in by the effect runner —
        #    keeps the adapter testable without reading ambient env)
        commit = self._git(
            [
                "-c",
                f"user.name={author_name}",
                "-c",
                f"user.email={author_email}",
                "commit",
                "-m",
                message,
            ],
            cwd=location.workspace,
        )
        if commit.returncode != 0:
            return CommitFilesResult(
                ok=False,
                branch=branch,
                commit_sha="",
                committed_paths=(),
                reason=f"git commit failed: {_first_line(commit.stderr)}",
            )

        rev = self._git(["rev-parse", "HEAD"], cwd=location.workspace)
        if rev.returncode != 0:
            return CommitFilesResult(
                ok=False,
                branch=branch,
                commit_sha="",
                committed_paths=tuple(committed_paths),
                reason=f"rev-parse HEAD failed: {_first_line(rev.stderr)}",
            )

        return CommitFilesResult(
            ok=True,
            branch=branch,
            commit_sha=rev.stdout.strip(),
            committed_paths=tuple(committed_paths),
        )

    # ----------------------------------------------------------------- open_pr_draft

    def open_pr_draft(
        self,
        *,
        location: RepoLocation,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> OpenPRDraftResult:
        # 1. push the branch — surfaces network / permission errors before we touch gh
        push = self._git(["push", "-u", "origin", branch], cwd=location.workspace)
        if push.returncode != 0:
            return OpenPRDraftResult(
                ok=False,
                pr_number=None,
                pr_url=None,
                branch=branch,
                base_branch=base_branch,
                reason=f"push rejected: {_first_line(push.stderr)}",
            )

        # 2. create the draft PR. `--draft` is hardcoded — never settable (CLAUDE.md #8)
        pr = self._gh(
            [
                "pr",
                "create",
                "--draft",
                "--base",
                base_branch,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
                "--repo",
                location.host_repo,
            ],
            cwd=location.workspace,
        )
        if pr.returncode != 0:
            return OpenPRDraftResult(
                ok=False,
                pr_number=None,
                pr_url=None,
                branch=branch,
                base_branch=base_branch,
                reason=f"gh pr create failed: {_first_line(pr.stderr)}",
            )

        # gh prints the PR URL on the first stdout line
        first = _first_line(pr.stdout).strip()
        if not first:
            return OpenPRDraftResult(
                ok=False,
                pr_number=None,
                pr_url=None,
                branch=branch,
                base_branch=base_branch,
                reason="gh pr create returned no URL",
            )
        match = _PR_URL_RE.search(first)
        pr_number = int(match.group(1)) if match else None

        return OpenPRDraftResult(
            ok=True,
            pr_number=pr_number,
            pr_url=first,
            branch=branch,
            base_branch=base_branch,
        )

    # ----------------------------------------------------------------- helpers

    def _git(self, args: list[str], *, cwd: Path) -> _SubprocResult:
        return _run(
            [self.git_binary, *args],
            cwd=cwd,
            timeout=self.default_timeout,
            max_bytes=self.max_output_bytes,
        )

    def _gh(self, args: list[str], *, cwd: Path) -> _SubprocResult:
        return _run(
            [self.gh_binary, *args],
            cwd=cwd,
            timeout=self.default_timeout,
            max_bytes=self.max_output_bytes,
        )


__all__ = ["GitHubRepoAdapter"]
