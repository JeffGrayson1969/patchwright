"""open_draft_pr_effect — post-transition side-effect that opens a draft PR.

Registered against (PATCH_APPLIED, AWAITING_REVIEW). Reads the patch_apply
agent's PatchApplyResult artifact, invokes the configured RepoAdapter to cut
a feature branch + commit the patched files + open a draft PR on GitHub, and
journals each step as an `artifact_written` entry.

Three properties this module owes the rest of the system:

1. **Never raises.** Every expected failure (gh missing, dirty tree, push
   rejected, etc.) is journaled as a `pr_draft_failed` step. The
   TransitionEffects.run() outer catch handles unexpected bugs; this module
   should never reach that catch.

2. **Never changes FSM state.** The case is already at AWAITING_REVIEW when the
   effect fires; any failure here leaves it there for the reviewer to retry
   from the CLI. The FSM is the agent's domain; effects only write
   artifact_written entries.

3. **Idempotent.** If a `pr_drafted` step is already in the journal, the
   effect short-circuits. "Fix gh auth + re-run patchwright drive" is a
   one-command recovery (per plan §7 open question #5).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from patchwright.core.fsm import State
from patchwright.core.orchestrator import (
    EffectContext,
    TransitionEffects,
    replay,
)
from patchwright.core.repo import RepoConfigError, RepoLocation, default_repo_adapter
from patchwright.core.reviews import reviewer_identity
from patchwright.models.patch_apply_result import PatchApplyResult

log = logging.getLogger(__name__)

_PR_DRAFTED_KIND = "pr_drafted"
_PR_DRAFT_FAILED_KIND = "pr_draft_failed"
_BRANCH_CREATED_KIND = "branch_created"
_COMMIT_PUSHED_KIND = "commit_pushed"

_DETECT_REPO_TIMEOUT = 15.0
_GIT_CONFIG_TIMEOUT = 5.0


# --------------------------------------------------------------------------- registration


def register_default_effects(effects: TransitionEffects) -> None:
    """Wire the production effects for M2-pr.

    Call this from the CLI's drive() entry point; tests opt in selectively.
    Existing P0 / tests that pass no effects to `drive()` are unaffected.
    """
    effects.register(
        (str(State.PATCH_APPLIED), str(State.AWAITING_REVIEW)),
        open_draft_pr_effect,
    )


# --------------------------------------------------------------------------- the effect


def open_draft_pr_effect(ctx: EffectContext) -> None:  # noqa: PLR0911 — one early-return per step
    """Open a draft PR for the case's PatchApplyResult.

    Sequence (each step journals its outcome):
      0. idempotency check: pr_drafted already journaled? short-circuit
      1. load PatchApplyResult artifact
      2. build RepoAdapter via default_repo_adapter(config)
      3. adapter.is_available()
      4. detect host_repo (owner/name) via `gh repo view`
      5. adapter.create_branch(...)
      6. adapter.commit_files(...) (reads files from scratch_dir)
      7. adapter.open_pr_draft(...)
      8. cleanup scratch_dir on success
    """
    if _has_journaled_kind(ctx, _PR_DRAFTED_KIND):
        log.debug("pr_drafted already journaled for case=%r; short-circuiting", ctx.case.id)
        return

    result = _load_latest_patch_apply_result(ctx)
    if result is None:
        _journal_step(
            ctx,
            _PR_DRAFT_FAILED_KIND,
            {"step": "load_result", "reason": "no patch_apply_result artifact on case"},
        )
        return

    try:
        adapter = default_repo_adapter(ctx.config)
    except RepoConfigError as exc:
        _journal_step(
            ctx,
            _PR_DRAFT_FAILED_KIND,
            {"step": "build_adapter", "reason": str(exc)},
        )
        return

    if not adapter.is_available():
        _journal_step(
            ctx,
            _PR_DRAFT_FAILED_KIND,
            {
                "step": "is_available",
                "reason": "gh not found on PATH or 'gh auth status' reports unauthenticated",
            },
        )
        return

    host_repo = _detect_host_repo(ctx.workspace_root)
    if not host_repo:
        _journal_step(
            ctx,
            _PR_DRAFT_FAILED_KIND,
            {
                "step": "detect_host_repo",
                "reason": (
                    "could not detect GitHub remote ('gh repo view --json nameWithOwner' failed)"
                ),
            },
        )
        return

    location = RepoLocation(workspace=ctx.workspace_root, host_repo=host_repo)
    author_name, author_email = _resolve_author()

    cb = adapter.create_branch(location=location, branch=result.branch_name)
    if not cb.ok:
        _journal_step(ctx, _PR_DRAFT_FAILED_KIND, {"step": "create_branch", "reason": cb.reason})
        return
    ctx = _journal_step(
        ctx,
        _BRANCH_CREATED_KIND,
        {"branch": cb.branch, "base_sha": cb.base_sha},
    )

    files = _read_modified_files(
        Path(result.scratch_dir), result.modified_files, ctx.workspace_root
    )

    cf = adapter.commit_files(
        location=location,
        branch=cb.branch,
        files=files,
        message=result.commit_message,
        author_name=author_name,
        author_email=author_email,
    )
    if not cf.ok:
        _journal_step(ctx, _PR_DRAFT_FAILED_KIND, {"step": "commit_files", "reason": cf.reason})
        return
    ctx = _journal_step(
        ctx,
        _COMMIT_PUSHED_KIND,
        {
            "branch": cf.branch,
            "commit_sha": cf.commit_sha,
            "committed_paths": list(cf.committed_paths),
        },
    )

    title = result.commit_message.splitlines()[0] if result.commit_message else "PatchWright patch"
    body = _format_pr_body(result)

    pr = adapter.open_pr_draft(
        location=location,
        branch=cb.branch,
        base_branch=result.base_branch,
        title=title,
        body=body,
    )
    if not pr.ok:
        _journal_step(ctx, _PR_DRAFT_FAILED_KIND, {"step": "open_pr_draft", "reason": pr.reason})
        return
    _journal_step(
        ctx,
        _PR_DRAFTED_KIND,
        {
            "pr_number": pr.pr_number,
            "pr_url": pr.pr_url,
            "branch": pr.branch,
            "base_branch": pr.base_branch,
        },
    )

    _cleanup_scratch(Path(result.scratch_dir))


# --------------------------------------------------------------------------- helpers


def _has_journaled_kind(ctx: EffectContext, kind_marker: str) -> bool:
    """True if any artifact_written entry on this case has payload.kind == kind_marker."""
    for entry in ctx.journal.read():
        if entry.kind == "artifact_written" and entry.payload.get("kind") == kind_marker:
            return True
    return False


def _load_latest_patch_apply_result(ctx: EffectContext) -> PatchApplyResult | None:
    latest_id: str | None = None
    for artifact in ctx.case.artifacts:
        if artifact.kind == "patch_apply_result":
            latest_id = artifact.id
    if latest_id is None:
        return None
    return PatchApplyResult.model_validate_json(ctx.store.get(latest_id))


def _resolve_author() -> tuple[str, str]:
    """Returns (name, email). Reuses core/reviews.reviewer_identity() for email;
    name from `git config user.name` with a sensible fallback."""
    email = reviewer_identity()
    name = _git_user_name() or email.split("@", 1)[0]
    return name, email


def _git_user_name() -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_CONFIG_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def _detect_host_repo(workspace: Path) -> str | None:
    """Return 'owner/name' for the GitHub repo at `workspace`, or None on failure."""
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=_DETECT_REPO_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name_with_owner = result.stdout.strip()
    return name_with_owner or None


def _read_modified_files(
    scratch_dir: Path,
    modified_files: tuple[str, ...],
    workspace: Path,
) -> dict[Path, str]:
    """Read each modified file from the scratch dir and key it by its destination
    inside the workspace. Matches GitHubRepoAdapter.commit_files()'s input shape."""
    out: dict[Path, str] = {}
    workspace_resolved = workspace.resolve()
    for rel in modified_files:
        src = scratch_dir / rel
        content = src.read_text(encoding="utf-8") if src.is_file() else ""
        dest = (workspace_resolved / rel).resolve()
        out[dest] = content
    return out


def _format_pr_body(result: PatchApplyResult) -> str:
    """Render the draft PR body. Operators iterate the full evidence pack via
    `patchwright review` — keep this focused on what reviewers want at a glance."""
    parts: list[str] = []
    parts.append("## Summary")
    parts.append(result.commit_message)
    parts.append("")
    parts.append("## Test result")
    parts.append(f"- exit_code: {result.test_result.exit_code}")
    parts.append(f"- timed_out: {result.test_result.timed_out}")
    if result.modified_files:
        parts.append("")
        parts.append("## Modified files")
        for f in result.modified_files:
            parts.append(f"- `{f}`")
    parts.append("")
    parts.append(
        "_Draft PR opened automatically by PatchWright. Mark ready for review when satisfied._"
    )
    return "\n".join(parts)


def _journal_step(ctx: EffectContext, kind_marker: str, data: dict[str, Any]) -> EffectContext:
    """Append an artifact_written entry tagged with `kind_marker` and return a
    fresh EffectContext reflecting the post-append case state.

    Effects must use the returned ctx for any subsequent journal calls — the
    last_seq / last_hash advanced. Returning a new ctx (rather than mutating)
    keeps EffectContext frozen and makes ordering explicit at the call site.
    """
    case = ctx.case
    payload: dict[str, Any] = {"kind": kind_marker, **data}
    ctx.journal.append(
        case_id=case.id,
        kind="artifact_written",
        author="system:effect_runner",
        payload=payload,
        prev_hash=case.last_hash,
        seq=case.last_seq + 1,
    )
    new_case = replay(ctx.journal, ctx.store)
    if new_case is None:  # pragma: no cover - impossible after we just appended
        raise RuntimeError("replay returned None after appending effect step")
    return EffectContext(
        case=new_case,
        store=ctx.store,
        journal=ctx.journal,
        config=ctx.config,
        workspace_root=ctx.workspace_root,
    )


def _cleanup_scratch(scratch_dir: Path) -> None:
    """Best-effort scratch-dir cleanup after a successful pr_drafted. Failures
    here don't affect the success of the effect — the dir is just garbage."""
    try:
        if scratch_dir.is_dir():
            shutil.rmtree(scratch_dir)
    except OSError as exc:
        log.warning("failed to cleanup scratch dir %s: %s", scratch_dir, exc)


__all__ = ["open_draft_pr_effect", "register_default_effects"]
