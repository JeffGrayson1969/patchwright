"""patch_apply agent — materializes a PatchPlan, runs tests, emits PatchApplyResult.

Drives PATCH_APPLIED -> AWAITING_REVIEW on test pass, PATCH_APPLIED -> REJECTED
on test fail or codemod-apply failure. The agent itself does NOT call gh or any
RepoAdapter — the post-transition effect runner in AEG-425 reads the
PatchApplyResult artifact and opens the draft PR. This keeps the agent pure
(CLAUDE.md #3 — agents return bytes, never touch external state).

Routing rules:
  - cross_check verdict != "approve"  -> REJECTED (no test run)
  - codemod / test-gen raises         -> REJECTED (no test run)
  - test_command exits 0              -> AWAITING_REVIEW
  - test_command exits non-zero       -> REJECTED
  - test_command times out            -> REJECTED

The scratch worktree is materialized at <case_root>/scratch/<case_id>/worktree/.
The repo_root is never modified — the codemod runs purely in memory, then we
copy repo_root to the scratch dir and overlay the new file contents on the copy.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.config import PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.models import AgentResult, Artifact, Case, Transition
from patchwright.core.sandbox import Mount, SandboxRunner
from patchwright.models.cross_check import CrossCheckVerdict
from patchwright.models.patch_apply_result import PatchApplyResult, TestResult
from patchwright.models.patch_plan import PatchPlan
from patchwright.tools import codemod_python, test_gen_python
from patchwright.tools.codemod_python import CodemodError

log = logging.getLogger(__name__)

_TEST_TIMEOUT_SECONDS = 120.0
_STDIO_TAIL_BYTES = 4096


@dataclass
class PatchApplyAgent:
    """Stateless agent. Rehydrates from disk every call (CLAUDE.md #3)."""

    repo_root: Path
    """Source tree the codemod is applied against. NEVER modified by this agent."""

    sandbox: SandboxRunner
    """Used to run `config.conventions.test_command` against the scratch worktree."""

    case_root: Path
    """Per-case data dir. Scratch worktrees live under <case_root>/scratch/<case_id>/worktree/."""

    config: PatchwrightConfig = field(default_factory=PatchwrightConfig)
    name: str = "patch_apply"
    handles_state: str = field(default=str(State.PATCH_APPLIED))

    def __call__(self, case: Case, store: ReadOnlyArtifactStore) -> AgentResult:
        plan, plan_artifact_id = _load_patch_plan(case, store)
        verdict = _load_cross_check(case, store)

        if verdict.verdict != "approve":
            return _reject(
                case,
                reason=f"cross_checker did not approve: {verdict.verdict}",
            )

        try:
            modified, diff_text = _materialize_patch(self.repo_root, plan)
        except CodemodError as exc:
            log.info("patch_apply codemod_failed case=%r err=%s", case.id, exc)
            return _reject(case, reason=f"codemod failed: {exc}")
        except ValueError as exc:
            # test_gen rejects malformed TestSpec; treat the same as codemod failure
            log.info("patch_apply test_gen_failed case=%r err=%s", case.id, exc)
            return _reject(case, reason=f"test generation failed: {exc}")

        scratch_dir = _materialize_scratch(self.repo_root, self.case_root, case.id, modified)

        run = self.sandbox.run(
            image=self.config.conventions.test_image,
            cmd=self.config.conventions.test_command.split(),
            mounts=[Mount(source=scratch_dir, target="/work", readonly=False)],
            timeout=_TEST_TIMEOUT_SECONDS,
        )

        repo_resolved = self.repo_root.resolve()
        modified_rel = tuple(sorted(p.relative_to(repo_resolved).as_posix() for p in modified))

        result = PatchApplyResult(
            case_id=case.id,
            plan_artifact_id=plan_artifact_id,
            modified_files=modified_rel,
            diff=diff_text,
            test_result=TestResult(
                exit_code=run.exit_code,
                stdout_tail=run.stdout[-_STDIO_TAIL_BYTES:],
                stderr_tail=run.stderr[-_STDIO_TAIL_BYTES:],
                timed_out=run.timed_out,
            ),
            scratch_dir=str(scratch_dir),
            branch_name=_branch_name(self.config.conventions.branch_prefix, case.id),
            base_branch=self.config.repo.default_base_branch,
            commit_message=_commit_message(plan),
        )
        result_bytes = canonical_json(result.model_dump(mode="json"))

        if run.timed_out or run.exit_code != 0:
            log.info(
                "patch_apply test_failed exit=%d timed_out=%s case=%r",
                run.exit_code,
                run.timed_out,
                case.id,
            )
            return AgentResult(
                transition=Transition(
                    case_id=case.id,
                    from_state=str(State.PATCH_APPLIED),
                    to_state=str(State.REJECTED),
                    reason=f"tests failed (exit={run.exit_code}, timed_out={run.timed_out})",
                ),
                new_artifacts=[(result_bytes, "patch_apply_result")],
                reason="test_failed",
            )

        log.info("patch_apply test_passed files=%d case=%r", len(modified_rel), case.id)
        return AgentResult(
            transition=Transition(
                case_id=case.id,
                from_state=str(State.PATCH_APPLIED),
                to_state=str(State.AWAITING_REVIEW),
                reason=f"patch_apply: tests pass on {len(modified_rel)} files",
            ),
            new_artifacts=[(result_bytes, "patch_apply_result")],
            reason="ok",
        )


# --------------------------------------------------------------------------- helpers


def _load_patch_plan(case: Case, store: ReadOnlyArtifactStore) -> tuple[PatchPlan, str]:
    """Find the PatchPlan artifact and return (plan, artifact_id)."""
    for artifact in case.artifacts:
        if artifact.kind == "patch_plan":
            return PatchPlan.model_validate_json(store.get(artifact.id)), artifact.id
    raise ValueError(f"case {case.id!r} has no patch_plan artifact; patch_apply cannot run")


def _load_cross_check(case: Case, store: ReadOnlyArtifactStore) -> CrossCheckVerdict:
    """Find the latest cross_check_verdict artifact. Cross-checker only ever
    appends one but we take the last as defense against future re-evaluation."""
    latest: Artifact | None = None
    for artifact in case.artifacts:
        if artifact.kind == "cross_check_verdict":
            latest = artifact
    if latest is None:
        raise ValueError(
            f"case {case.id!r} has no cross_check_verdict artifact; patch_apply cannot run"
        )
    return CrossCheckVerdict.model_validate_json(store.get(latest.id))


def _materialize_patch(repo_root: Path, plan: PatchPlan) -> tuple[dict[Path, str], str]:
    """Run codemod + test-gen purely (no disk writes). Returns (modified_files, diff)."""
    modified = codemod_python.apply(plan, repo_root)
    diff_text = codemod_python.diff(repo_root, modified)

    if plan.test_spec is not None:
        test_source = test_gen_python.render(plan.test_spec)
        test_path = _resolve_inside(repo_root, plan.test_spec.file)
        modified[test_path] = test_source
        # Recompute diff to include the new test file
        diff_text = codemod_python.diff(repo_root, modified)

    return modified, diff_text


def _resolve_inside(repo_root: Path, relpath: str) -> Path:
    """Resolve `relpath` against `repo_root` and reject any traversal."""
    repo_resolved = repo_root.resolve()
    candidate = (repo_resolved / relpath).resolve()
    try:
        candidate.relative_to(repo_resolved)
    except ValueError as exc:
        raise CodemodError(f"test_spec.file escapes repo root: {relpath!r}") from exc
    return candidate


def _materialize_scratch(
    repo_root: Path,
    case_root: Path,
    case_id: str,
    modified: dict[Path, str],
) -> Path:
    """Copy repo_root to <case_root>/scratch/<case_id>/worktree/ and overlay the
    modified file contents. Idempotent — recreates the scratch dir on every call
    so a re-run after a fix produces a clean tree."""
    scratch = case_root / "scratch" / case_id / "worktree"
    if scratch.exists():
        shutil.rmtree(scratch)
    shutil.copytree(repo_root, scratch, symlinks=False)

    repo_resolved = repo_root.resolve()
    for path, content in modified.items():
        try:
            rel = path.relative_to(repo_resolved)
        except ValueError:
            raise CodemodError(f"modified path escapes repo_root: {path}") from None
        dest = scratch / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    return scratch.resolve()


def _branch_name(prefix: str, case_id: str) -> str:
    """patchwright/case-<short-id>. case_id is 'case-<12hex>' from stable_case_id()."""
    suffix = case_id.removeprefix("case-")[:12]
    return f"{prefix.rstrip('/')}/case-{suffix}"


def _commit_message(plan: PatchPlan) -> str:
    """Full conventional-commit body: summary line + rationale paragraph."""
    return f"{plan.summary}\n\n{plan.rationale}"


def _reject(case: Case, *, reason: str) -> AgentResult:
    """Build a PATCH_APPLIED -> REJECTED transition with no artifact."""
    return AgentResult(
        transition=Transition(
            case_id=case.id,
            from_state=str(State.PATCH_APPLIED),
            to_state=str(State.REJECTED),
            reason=reason,
        ),
        new_artifacts=[],
        reason=reason,
    )


__all__ = ["PatchApplyAgent"]
