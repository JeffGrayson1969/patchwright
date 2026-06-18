"""patch_apply agent — fake SandboxRunner, pre-seeded artifacts (AEG-424).

Covers the routing matrix:
  - cross_check approve + tests pass    -> AWAITING_REVIEW
  - cross_check approve + tests fail    -> REJECTED (artifact still emitted)
  - cross_check approve + tests timeout -> REJECTED (artifact still emitted)
  - cross_check refuse                  -> REJECTED (no test run, no artifact)
  - codemod failure                     -> REJECTED (no test run)

Plus structural checks:
  - Agent satisfies the Agent Protocol
  - handles_state is PATCH_APPLIED
  - PatchApplyResult artifact shape (paths, branch, base, commit message)
  - Scratch worktree materialized at <case_root>/scratch/<case_id>/worktree
  - repo_root is NEVER modified (the agent's promise)
  - Missing patch_plan / cross_check_verdict artifacts raise ValueError
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import patchwright.agents.patch_apply as patch_apply_module
from patchwright.agents.patch_apply import PatchApplyAgent, _branch_name, _commit_message
from patchwright.core.artifacts import ArtifactStore, ReadOnlyArtifactStore
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.models import Artifact, Case
from patchwright.core.protocols import Agent
from patchwright.core.sandbox import Mount, NetworkPolicy, ResourceLimits, RunResult
from patchwright.models.cross_check import CrossCheckVerdict
from patchwright.models.patch_apply_result import PatchApplyResult
from patchwright.models.patch_plan import InsertImport, PatchPlan

# --------------------------------------------------------------------------- fakes


@dataclass
class FakeSandbox:
    """Records the last call args; returns the configured next_result."""

    next_result: RunResult | None = None
    last_call: dict[str, Any] = field(default_factory=dict)
    name: str = "fake"

    def is_available(self) -> bool:
        return True

    def run(
        self,
        *,
        image: str,
        cmd: list[str],
        mounts: list[Mount] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        network_policy: NetworkPolicy | None = None,
        resource_limits: ResourceLimits | None = None,
    ) -> RunResult:
        self.last_call = {
            "image": image,
            "cmd": cmd,
            "mounts": mounts or [],
            "env": env or {},
            "timeout": timeout,
        }
        if self.next_result is None:
            raise RuntimeError("FakeSandbox has no next_result configured")
        return self.next_result


def _success_result(image: str = "python:3.12-slim") -> RunResult:
    return RunResult(
        exit_code=0,
        stdout="all tests pass\n",
        stderr="",
        timed_out=False,
        image=image,
        cmd=("pytest",),
    )


def _failure_result(image: str = "python:3.12-slim") -> RunResult:
    return RunResult(
        exit_code=1,
        stdout="",
        stderr="AssertionError\n",
        timed_out=False,
        image=image,
        cmd=("pytest",),
    )


def _timeout_result(image: str = "python:3.12-slim") -> RunResult:
    return RunResult(
        exit_code=-1,
        stdout="",
        stderr="test timed out\n",
        timed_out=True,
        image=image,
        cmd=("pytest",),
    )


# --------------------------------------------------------------------------- factories


def _make_patch_plan(case_id: str) -> PatchPlan:
    """Minimal PatchPlan with an InsertImport — the codemod handles it cleanly
    and adds one line to the target file."""
    return PatchPlan(
        case_id=case_id,
        summary="Add safety import",
        operations=[InsertImport(file="x.py", module="safe_helpers", names=["sanitize"])],
        rationale="sanitize ensures inputs are validated before use.",
    )


def _make_verdict(verdict: str = "approve") -> CrossCheckVerdict:
    return CrossCheckVerdict(
        vulnerability_summary="path traversal in x.read",
        fix_summary="wraps x.read with sanitize() to enforce containment",
        verdict=verdict,  # type: ignore[arg-type]
        reasoning="closes the unsafe-path code path described in the report",
        confidence=0.9,
    )


def _seed_repo(repo_root: Path) -> None:
    """Minimal source tree the codemod can apply against."""
    (repo_root / "x.py").write_text(
        "from __future__ import annotations\n\n\ndef read(p): return open(p).read()\n",
        encoding="utf-8",
    )


def _make_case(
    *,
    tmp_path: Path,
    case_id: str = "case-abc123def456",
    plan: PatchPlan | None = None,
    verdict: CrossCheckVerdict | None = None,
) -> tuple[Case, ReadOnlyArtifactStore, ArtifactStore]:
    plan = plan or _make_patch_plan(case_id)
    store = ArtifactStore(tmp_path / "artifacts")

    artifacts: list[Artifact] = []
    plan_bytes = canonical_json(plan.model_dump(mode="json"))
    plan_sha = store.put(plan_bytes)
    artifacts.append(Artifact(id=plan_sha, kind="patch_plan", size=len(plan_bytes)))

    if verdict is not None:
        verdict_bytes = canonical_json(verdict.model_dump(mode="json"))
        verdict_sha = store.put(verdict_bytes)
        artifacts.append(
            Artifact(id=verdict_sha, kind="cross_check_verdict", size=len(verdict_bytes))
        )

    case = Case(
        id=case_id,
        state=str(State.PATCH_APPLIED),
        created_at="2026-06-17T00:00:00.000000Z",
        artifacts=artifacts,
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    return case, store.read_only(), store


def _make_agent(*, repo_root: Path, tmp_path: Path, sandbox: FakeSandbox) -> PatchApplyAgent:
    return PatchApplyAgent(
        repo_root=repo_root,
        sandbox=sandbox,
        case_root=tmp_path / "case_root",
    )


# --------------------------------------------------------------------------- structural


def test_agent_satisfies_protocol(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=FakeSandbox())
    assert isinstance(agent, Agent)


def test_agent_handles_patch_applied_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=FakeSandbox())
    assert agent.handles_state == str(State.PATCH_APPLIED)


# --------------------------------------------------------------------------- happy path


def test_approve_and_tests_pass_routes_to_awaiting_review(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    sandbox = FakeSandbox(next_result=_success_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.from_state == str(State.PATCH_APPLIED)
    assert result.transition.to_state == str(State.AWAITING_REVIEW)
    assert result.reason == "ok"
    assert len(result.new_artifacts) == 1
    artifact_bytes, kind = result.new_artifacts[0]
    assert kind == "patch_apply_result"
    parsed = PatchApplyResult.model_validate_json(artifact_bytes)
    assert parsed.case_id == case.id
    assert parsed.test_result.exit_code == 0
    assert parsed.test_result.timed_out is False
    assert parsed.modified_files == ("x.py",)
    assert parsed.branch_name == "patchwright/case-abc123def456"
    assert parsed.base_branch == "main"
    assert parsed.commit_message.startswith("Add safety import\n\n")


def test_sandbox_invoked_with_configured_image_and_command(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    sandbox = FakeSandbox(next_result=_success_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)
    agent(case, ro_store)

    assert sandbox.last_call["image"] == "python:3.12-slim"
    assert sandbox.last_call["cmd"] == ["pytest"]
    mounts = sandbox.last_call["mounts"]
    assert len(mounts) == 1
    assert mounts[0].target == "/work"
    assert mounts[0].readonly is False


def test_scratch_worktree_materialized_at_expected_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    sandbox = FakeSandbox(next_result=_success_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)
    result = agent(case, ro_store)

    parsed = PatchApplyResult.model_validate_json(result.new_artifacts[0][0])
    scratch = Path(parsed.scratch_dir)
    expected = (tmp_path / "case_root" / "scratch" / case.id / "worktree").resolve()
    assert scratch == expected
    assert scratch.exists()
    assert (scratch / "x.py").exists()
    # patched content present in scratch
    assert "safe_helpers" in (scratch / "x.py").read_text()


def test_repo_root_is_never_modified(tmp_path: Path) -> None:
    """The agent's central promise: it does NOT touch repo_root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    original = (repo / "x.py").read_text()

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    sandbox = FakeSandbox(next_result=_success_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)
    agent(case, ro_store)

    assert (repo / "x.py").read_text() == original


def test_plan_artifact_id_carried_into_result(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    case, ro_store, store = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    plan_id = next(a.id for a in case.artifacts if a.kind == "patch_plan")
    assert store.has(plan_id)

    sandbox = FakeSandbox(next_result=_success_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)
    result = agent(case, ro_store)

    parsed = PatchApplyResult.model_validate_json(result.new_artifacts[0][0])
    assert parsed.plan_artifact_id == plan_id


# --------------------------------------------------------------------------- test-fail routes


def test_test_failure_routes_to_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    sandbox = FakeSandbox(next_result=_failure_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.REJECTED)
    assert result.reason == "test_failed"
    # Artifact IS still emitted so reviewers can see what failed
    assert len(result.new_artifacts) == 1
    parsed = PatchApplyResult.model_validate_json(result.new_artifacts[0][0])
    assert parsed.test_result.exit_code == 1
    assert "AssertionError" in parsed.test_result.stderr_tail


def test_test_timeout_routes_to_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    sandbox = FakeSandbox(next_result=_timeout_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.REJECTED)
    parsed = PatchApplyResult.model_validate_json(result.new_artifacts[0][0])
    assert parsed.test_result.timed_out is True


# --------------------------------------------------------------------------- cross-check refuse


def test_refuse_verdict_routes_to_rejected_without_running_tests(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict(verdict="refuse"))
    sandbox = FakeSandbox(next_result=_success_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.REJECTED)
    assert "cross_checker did not approve" in result.transition.reason
    assert result.new_artifacts == []
    assert sandbox.last_call == {}  # never called


# --------------------------------------------------------------------------- codemod failure


def test_codemod_failure_routes_to_rejected_without_running_tests(tmp_path: Path) -> None:
    """If the codemod can't apply (e.g. target file missing), reject — never run tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # NOTE: x.py NOT seeded — codemod will raise

    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=_make_verdict())
    sandbox = FakeSandbox(next_result=_success_result())
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=sandbox)

    result = agent(case, ro_store)

    assert result.transition.to_state == str(State.REJECTED)
    assert "codemod failed" in result.transition.reason
    assert result.new_artifacts == []
    assert sandbox.last_call == {}  # never called


# --------------------------------------------------------------------------- missing artifacts


def test_missing_patch_plan_artifact_raises(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    case = Case(
        id="case-empty",
        state=str(State.PATCH_APPLIED),
        created_at="2026-06-17T00:00:00.000000Z",
        artifacts=[],
        last_seq=0,
        last_hash="sha256:" + "0" * 64,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=FakeSandbox())
    with pytest.raises(ValueError, match="no patch_plan artifact"):
        agent(case, store.read_only())


def test_missing_cross_check_artifact_raises(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    # build a case with only a patch_plan artifact, no cross_check_verdict
    case, ro_store, _ = _make_case(tmp_path=tmp_path, verdict=None)
    agent = _make_agent(repo_root=repo, tmp_path=tmp_path, sandbox=FakeSandbox())
    with pytest.raises(ValueError, match="no cross_check_verdict"):
        agent(case, ro_store)


# --------------------------------------------------------------------------- pure helpers


def test_branch_name_uses_stable_case_short_id() -> None:
    assert _branch_name("patchwright/", "case-abc123def456") == "patchwright/case-abc123def456"


def test_branch_name_respects_custom_prefix() -> None:
    assert _branch_name("fixes/", "case-abc123def456") == "fixes/case-abc123def456"


def test_branch_name_strips_trailing_slash_on_prefix() -> None:
    assert _branch_name("patchwright///", "case-x") == "patchwright/case-x"


def test_commit_message_includes_summary_and_rationale() -> None:
    plan = _make_patch_plan("case-x")
    msg = _commit_message(plan)
    assert msg.startswith(plan.summary)
    assert plan.rationale in msg


# --------------------------------------------------------------------------- structural enforcement


def test_agent_does_not_import_repo_adapter_module() -> None:
    """CLAUDE.md #3: agents are pure. patch_apply must not import
    patchwright.adapters.* (that's the effect runner's job in AEG-425)."""
    source = inspect.getsource(patch_apply_module)
    assert "patchwright.adapters" not in source
    assert "import GitHubRepoAdapter" not in source


def test_agent_does_not_import_repo_module() -> None:
    """Same logic for core.repo — the agent emits an artifact for the effect
    runner to consume; it has no business calling RepoAdapter methods directly."""
    source = inspect.getsource(patch_apply_module)
    assert "from patchwright.core.repo" not in source
