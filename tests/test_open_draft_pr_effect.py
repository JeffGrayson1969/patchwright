"""open_draft_pr_effect — fake RepoAdapter; covers full sequence + every failure mode.

Verifies the AEG-425 effect:
  - happy path: branch_created -> commit_pushed -> pr_drafted entries journaled
  - idempotency: re-running short-circuits on existing pr_drafted entry
  - load_result failure (no patch_apply_result on case)
  - adapter unavailable (is_available -> False)
  - host_repo detection failure (gh repo view returns empty)
  - create_branch failure
  - commit_files failure
  - open_pr_draft failure
  - FSM state is unchanged by any failure path
  - hash chain stays intact
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.config import PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import GENESIS_HASH, canonical_json
from patchwright.core.journal import Journal
from patchwright.core.models import Artifact
from patchwright.core.orchestrator import EffectContext
from patchwright.core.repo import (
    CommitFilesResult,
    CreateBranchResult,
    OpenPRDraftResult,
    RepoLocation,
)
from patchwright.core.repo_effects import open_draft_pr_effect, register_default_effects
from patchwright.models.patch_apply_result import PatchApplyResult, TestResult

# --------------------------------------------------------------------------- fake adapter


@dataclass
class FakeRepoAdapter:
    """Records call args and returns canned results."""

    name: str = "fake-github"
    available: bool = True
    create_result: CreateBranchResult | None = None
    commit_result: CommitFilesResult | None = None
    pr_result: OpenPRDraftResult | None = None
    create_calls: list[dict] = field(default_factory=list)
    commit_calls: list[dict] = field(default_factory=list)
    pr_calls: list[dict] = field(default_factory=list)
    availability_checks: int = 0

    def is_available(self) -> bool:
        self.availability_checks += 1
        return self.available

    def create_branch(
        self, *, location: RepoLocation, branch: str, base: str = "HEAD"
    ) -> CreateBranchResult:
        self.create_calls.append({"location": location, "branch": branch, "base": base})
        return self.create_result or CreateBranchResult(ok=True, branch=branch, base_sha="deadbeef")

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
        self.commit_calls.append(
            {
                "location": location,
                "branch": branch,
                "files": dict(files),
                "message": message,
                "author_name": author_name,
                "author_email": author_email,
            }
        )
        return self.commit_result or CommitFilesResult(
            ok=True,
            branch=branch,
            commit_sha="cafef00d",
            committed_paths=tuple(sorted(p.name for p in files)),
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
        self.pr_calls.append(
            {
                "location": location,
                "branch": branch,
                "base_branch": base_branch,
                "title": title,
                "body": body,
            }
        )
        return self.pr_result or OpenPRDraftResult(
            ok=True,
            pr_number=42,
            pr_url="https://github.com/owner/name/pull/42",
            branch=branch,
            base_branch=base_branch,
        )


# --------------------------------------------------------------------------- fixture builders


def _make_patch_apply_result(case_id: str, scratch_dir: Path) -> PatchApplyResult:
    return PatchApplyResult(
        case_id=case_id,
        plan_artifact_id="sha256:" + "a" * 64,
        modified_files=("x.py",),
        diff="--- a/x.py\n+++ b/x.py\n",
        test_result=TestResult(exit_code=0, timed_out=False),
        scratch_dir=str(scratch_dir),
        branch_name="patchwright/case-abc123def456",
        base_branch="main",
        commit_message="Add safety import\n\nWraps the unsafe call.",
    )


def _seed_case_with_patch_apply_result(
    *,
    tmp_path: Path,
    case_id: str = "case-abc123def456",
) -> tuple[EffectContext, Path]:
    """Build an EffectContext whose case has a patch_apply_result artifact and
    a populated scratch dir on disk."""
    root = tmp_path / "pw_root"
    artifacts_dir = root / "artifacts"
    journal_dir = root / "journal" / case_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    scratch_dir = tmp_path / "case_root" / "scratch" / case_id / "worktree"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    (scratch_dir / "x.py").write_text("from safe_helpers import sanitize\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    store = ArtifactStore(artifacts_dir)
    journal = Journal(journal_dir)

    # Bootstrap case_opened + a transition that lands us at AWAITING_REVIEW
    journal.append(
        case_id=case_id,
        kind="case_opened",
        author="system:orchestrator",
        payload={
            "initial_state": str(State.INTAKE),
            "created_at": "2026-06-18T00:00:00.000000Z",
        },
        prev_hash=GENESIS_HASH,
        seq=0,
    )

    result = _make_patch_apply_result(case_id, scratch_dir)
    result_bytes = canonical_json(result.model_dump(mode="json"))
    result_sha = store.put(result_bytes)
    result_artifact = Artifact(id=result_sha, kind="patch_apply_result", size=len(result_bytes))

    # Pretend an earlier transition emitted the patch_apply_result and landed us
    # at AWAITING_REVIEW. We use a dummy prev_hash from the previous append.
    entries = journal.read()
    last = entries[-1]
    journal.append(
        case_id=case_id,
        kind="transition",
        author="agent:patch_apply",
        payload={
            "from_state": str(State.PATCH_APPLIED),
            "to_state": str(State.AWAITING_REVIEW),
            "reason": "tests pass",
            "artifacts": [result_artifact.model_dump()],
        },
        prev_hash=last.content_hash,
        seq=last.seq + 1,
    )

    # Build the case via replay (importing replay would create a cycle; rebuild manually)
    from patchwright.core.orchestrator import replay  # noqa: PLC0415

    case = replay(journal, store)
    assert case is not None
    assert case.state == str(State.AWAITING_REVIEW)

    ctx = EffectContext(
        case=case,
        store=store,
        journal=journal,
        config=PatchwrightConfig(),
        workspace_root=workspace,
    )
    return ctx, scratch_dir


def _journal_kinds_of_artifact_written(ctx: EffectContext) -> list[str]:
    """Sequence of payload.kind values from every artifact_written entry in the journal."""
    return [e.payload.get("kind") for e in ctx.journal.read() if e.kind == "artifact_written"]


# --------------------------------------------------------------------------- registration


def test_register_default_effects_wires_the_pr_effect() -> None:
    from patchwright.core.orchestrator import TransitionEffects  # noqa: PLC0415

    effects = TransitionEffects()
    register_default_effects(effects)
    fns = effects.registered_for((str(State.PATCH_APPLIED), str(State.AWAITING_REVIEW)))
    assert open_draft_pr_effect in fns


# --------------------------------------------------------------------------- happy path


def test_happy_path_journals_full_sequence(tmp_path: Path) -> None:
    ctx, scratch_dir = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter()

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("Test User", "test@example.com"),
        ),
    ):
        open_draft_pr_effect(ctx)

    assert _journal_kinds_of_artifact_written(ctx) == [
        "branch_created",
        "commit_pushed",
        "pr_drafted",
    ]
    # adapter received expected calls
    assert adapter.availability_checks == 1
    assert len(adapter.create_calls) == 1
    assert adapter.create_calls[0]["branch"] == "patchwright/case-abc123def456"
    assert len(adapter.commit_calls) == 1
    commit_call = adapter.commit_calls[0]
    assert commit_call["author_name"] == "Test User"
    assert commit_call["author_email"] == "test@example.com"
    assert commit_call["message"].startswith("Add safety import")
    # Files dict carries the workspace-relative path with the patched contents
    files = commit_call["files"]
    assert len(files) == 1
    only_path = next(iter(files))
    assert only_path.name == "x.py"
    assert files[only_path] == "from safe_helpers import sanitize\n"
    # PR call uses the configured base branch and --draft via the adapter contract
    assert len(adapter.pr_calls) == 1
    pr_call = adapter.pr_calls[0]
    assert pr_call["base_branch"] == "main"
    assert pr_call["title"] == "Add safety import"
    # Scratch dir cleaned up on success
    assert not scratch_dir.exists()


def test_pr_drafted_payload_carries_url_and_number(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter()

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("X", "x@y"),
        ),
    ):
        open_draft_pr_effect(ctx)

    pr_drafted_entries = [
        e
        for e in ctx.journal.read()
        if e.kind == "artifact_written" and e.payload.get("kind") == "pr_drafted"
    ]
    assert len(pr_drafted_entries) == 1
    payload = pr_drafted_entries[0].payload
    assert payload["pr_number"] == 42
    assert payload["pr_url"] == "https://github.com/owner/name/pull/42"


# --------------------------------------------------------------------------- idempotency


def test_re_running_short_circuits_on_existing_pr_drafted(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter()

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("X", "x@y"),
        ),
    ):
        open_draft_pr_effect(ctx)
        # Re-load case from disk via replay; rebuild ctx for second invocation
        from patchwright.core.orchestrator import replay  # noqa: PLC0415

        case2 = replay(ctx.journal, ctx.store)
        assert case2 is not None
        ctx2 = EffectContext(
            case=case2,
            store=ctx.store,
            journal=ctx.journal,
            config=ctx.config,
            workspace_root=ctx.workspace_root,
        )
        open_draft_pr_effect(ctx2)

    # No second round of journal entries
    assert _journal_kinds_of_artifact_written(ctx) == [
        "branch_created",
        "commit_pushed",
        "pr_drafted",
    ]
    # adapter not re-invoked
    assert len(adapter.create_calls) == 1
    assert len(adapter.commit_calls) == 1
    assert len(adapter.pr_calls) == 1


# --------------------------------------------------------------------------- failure modes


def test_missing_patch_apply_result_artifact_journals_failure(tmp_path: Path) -> None:
    # build a case at AWAITING_REVIEW with NO patch_apply_result artifact
    case_id = "case-empty"
    root = tmp_path / "pw"
    journal_dir = root / "journal" / case_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(root / "artifacts")
    journal = Journal(journal_dir)
    journal.append(
        case_id=case_id,
        kind="case_opened",
        author="system:orchestrator",
        payload={
            "initial_state": str(State.AWAITING_REVIEW),
            "created_at": "2026-06-18T00:00:00.000000Z",
        },
        prev_hash=GENESIS_HASH,
        seq=0,
    )
    from patchwright.core.orchestrator import replay  # noqa: PLC0415

    case = replay(journal, store)
    assert case is not None
    ctx = EffectContext(
        case=case,
        store=store,
        journal=journal,
        config=PatchwrightConfig(),
        workspace_root=tmp_path,
    )
    open_draft_pr_effect(ctx)
    kinds = _journal_kinds_of_artifact_written(ctx)
    assert kinds == ["pr_draft_failed"]
    payload = ctx.journal.read()[-1].payload
    assert payload["step"] == "load_result"


def test_adapter_unavailable_journals_failure(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter(available=False)

    with patch("patchwright.core.repo_effects.default_repo_adapter", return_value=adapter):
        open_draft_pr_effect(ctx)

    kinds = _journal_kinds_of_artifact_written(ctx)
    assert kinds == ["pr_draft_failed"]
    payload = ctx.journal.read()[-1].payload
    assert payload["step"] == "is_available"


def test_host_repo_detection_failure_journals_failure(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter()

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value=None,
        ),
    ):
        open_draft_pr_effect(ctx)

    kinds = _journal_kinds_of_artifact_written(ctx)
    assert kinds == ["pr_draft_failed"]
    payload = ctx.journal.read()[-1].payload
    assert payload["step"] == "detect_host_repo"


def test_create_branch_failure_journals_failure(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter(
        create_result=CreateBranchResult(
            ok=False,
            branch="patchwright/case-abc123def456",
            base_sha="",
            reason="workspace has uncommitted changes; run on a clean tree",
        ),
    )

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("X", "x@y"),
        ),
    ):
        open_draft_pr_effect(ctx)

    kinds = _journal_kinds_of_artifact_written(ctx)
    assert kinds == ["pr_draft_failed"]
    payload = ctx.journal.read()[-1].payload
    assert payload["step"] == "create_branch"
    assert "uncommitted changes" in payload["reason"]


def test_commit_files_failure_journals_failure_after_branch_created(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter(
        commit_result=CommitFilesResult(
            ok=False,
            branch="patchwright/case-abc123def456",
            commit_sha="",
            committed_paths=(),
            reason="git commit failed: nothing to commit",
        ),
    )

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("X", "x@y"),
        ),
    ):
        open_draft_pr_effect(ctx)

    kinds = _journal_kinds_of_artifact_written(ctx)
    assert kinds == ["branch_created", "pr_draft_failed"]
    payload = ctx.journal.read()[-1].payload
    assert payload["step"] == "commit_files"


def test_open_pr_failure_journals_failure_after_commit_pushed(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter(
        pr_result=OpenPRDraftResult(
            ok=False,
            pr_number=None,
            pr_url=None,
            branch="patchwright/case-abc123def456",
            base_branch="main",
            reason="gh pr create failed: rate limited",
        ),
    )

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("X", "x@y"),
        ),
    ):
        open_draft_pr_effect(ctx)

    kinds = _journal_kinds_of_artifact_written(ctx)
    assert kinds == ["branch_created", "commit_pushed", "pr_draft_failed"]
    payload = ctx.journal.read()[-1].payload
    assert payload["step"] == "open_pr_draft"


# --------------------------------------------------------------------------- FSM invariant


@pytest.mark.parametrize(
    "scenario",
    ["unavailable", "create_branch_fail", "commit_files_fail", "open_pr_fail"],
)
def test_no_failure_path_changes_fsm_state(tmp_path: Path, scenario: str) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    state_before = ctx.case.state

    if scenario == "unavailable":
        adapter: FakeRepoAdapter = FakeRepoAdapter(available=False)
    elif scenario == "create_branch_fail":
        adapter = FakeRepoAdapter(
            create_result=CreateBranchResult(ok=False, branch="b", base_sha="", reason="dirty"),
        )
    elif scenario == "commit_files_fail":
        adapter = FakeRepoAdapter(
            commit_result=CommitFilesResult(
                ok=False, branch="b", commit_sha="", committed_paths=(), reason="x"
            ),
        )
    else:  # open_pr_fail
        adapter = FakeRepoAdapter(
            pr_result=OpenPRDraftResult(
                ok=False,
                pr_number=None,
                pr_url=None,
                branch="b",
                base_branch="main",
                reason="x",
            ),
        )

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("X", "x@y"),
        ),
    ):
        open_draft_pr_effect(ctx)

    # State invariant: only `transition` entries change case.state; artifact_written
    # entries journaled by the effect can never advance the FSM.
    from patchwright.core.orchestrator import replay  # noqa: PLC0415

    final = replay(ctx.journal, ctx.store)
    assert final is not None
    assert final.state == state_before


# --------------------------------------------------------------------------- hash chain


def test_journal_hash_chain_intact_after_full_run(tmp_path: Path) -> None:
    ctx, _ = _seed_case_with_patch_apply_result(tmp_path=tmp_path)
    adapter = FakeRepoAdapter()

    with (
        patch(
            "patchwright.core.repo_effects.default_repo_adapter",
            return_value=adapter,
        ),
        patch(
            "patchwright.core.repo_effects._detect_host_repo",
            return_value="owner/name",
        ),
        patch(
            "patchwright.core.repo_effects._resolve_author",
            return_value=("X", "x@y"),
        ),
    ):
        open_draft_pr_effect(ctx)

    # Journal.read() raises ChainBroken if any link is inconsistent.
    entries = ctx.journal.read()
    # case_opened + transition + 3 artifact_written
    assert len(entries) == 5
    # prev_hash linkage: every entry after [0] references the previous content_hash
    from itertools import pairwise  # noqa: PLC0415

    for prev, cur in pairwise(entries):
        assert cur.prev_hash == prev.content_hash
