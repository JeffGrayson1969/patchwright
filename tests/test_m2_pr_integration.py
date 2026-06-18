"""End-to-end M2-pr integration test (AEG-426, AEG-374 exit criterion).

Drives a real fixture case from PATCH_APPLIED -> AWAITING_REVIEW through
drive() with effects armed; asserts the full journal sequence
(transition -> branch_created -> commit_pushed -> pr_drafted) plus four
negative scenarios + the idempotency re-drive case.

What's real vs. stubbed:
  - Real `git`: tmp workspace with `git init`, an initial commit, and a
    `file://<tmp>/remote.git` bare remote so `git push -u origin <branch>`
    actually succeeds (no network).
  - Stub `gh`: a tmp Python script prepended to PATH that dispatches on
    `gh auth status`, `gh repo view`, `gh pr create`. Env vars steer the
    stub for the negative scenarios (auth fail, etc.).
  - Real codemod + agent + effect runner: the actual production code path
    runs end-to-end; only the external surfaces (gh, sandbox) are faked.
  - Fake SandboxRunner: the patch_apply agent's test runner returns success
    without touching Docker. (Real docker is exercised by
    test_docker_sandbox_integration.py.)

Skipped when `git` is missing on the host (rare; CI has it).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from patchwright.agents.patch_apply import PatchApplyAgent
from patchwright.core.artifacts import ArtifactStore
from patchwright.core.config import PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.journal import Journal
from patchwright.core.models import Artifact, Case
from patchwright.core.orchestrator import (
    TransitionEffects,
    case_root_paths,
    drive,
    open_case,
    replay,
)
from patchwright.core.registry import Registry
from patchwright.core.repo_effects import register_default_effects
from patchwright.core.sandbox import Mount, NetworkPolicy, ResourceLimits, RunResult
from patchwright.models.cross_check import CrossCheckVerdict
from patchwright.models.patch_plan import PatchPlan
from patchwright.models.triage import TriageDisposition, TriagePacket

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "patch_corpus" / "cwe22_path_traversal"
STUB_PR_URL = "https://github.com/test-owner/test-repo/pull/42"
STUB_REPO_NAME = "test-owner/test-repo"

# Python stub for `gh`. Reads env vars to fail injection-style for negative tests.
_GH_STUB = textwrap.dedent(
    f"""\
    #!/usr/bin/env python3
    \"\"\"Stub `gh` for M2-pr integration tests.

    Env-var dispatch keeps the script simple and lets each test parametrize
    failure scenarios without rewriting the file.

      STUB_GH_AUTH_FAIL=1     -> `gh auth status` exits 1
      STUB_GH_REPO_FAIL=1     -> `gh repo view` exits 1
      STUB_GH_PR_FAIL=1       -> `gh pr create` exits 1 (stderr "rate limited")
    \"\"\"
    import os
    import sys

    args = sys.argv[1:]
    env = os.environ

    if args[:2] == ["auth", "status"]:
        if env.get("STUB_GH_AUTH_FAIL"):
            sys.stderr.write("not authenticated\\n")
            sys.exit(1)
        print("logged in")
        sys.exit(0)

    if args[:2] == ["repo", "view"]:
        if env.get("STUB_GH_REPO_FAIL"):
            sys.stderr.write("no remote\\n")
            sys.exit(1)
        print(env.get("STUB_GH_REPO_NAME", "{STUB_REPO_NAME}"))
        sys.exit(0)

    if args[:2] == ["pr", "create"]:
        if env.get("STUB_GH_PR_FAIL"):
            sys.stderr.write("rate limited\\n")
            sys.exit(1)
        print(env.get("STUB_GH_PR_URL", "{STUB_PR_URL}"))
        sys.exit(0)

    sys.stderr.write(f"stub gh: unrecognized argv: {{args}}\\n")
    sys.exit(2)
    """
)


# --------------------------------------------------------------------------- workspace fixture


@dataclass
class _Workspace:
    """Real-git tmp workspace + bare remote for E2E PR-creation tests."""

    workspace: Path
    bare: Path
    initial_commit_sha: str


def _init_git_workspace(tmp_path: Path) -> _Workspace:
    """Seed a tmp workspace with the fixture's vulnerable.py + an initial commit,
    plus a bare remote so `git push origin <branch>` works locally."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    bare = tmp_path / "remote.git"
    bare.mkdir(parents=True, exist_ok=True)

    _run(["git", "init", "--bare", "--initial-branch=main", str(bare)])
    _run(["git", "init", "--initial-branch=main", str(workspace)])
    _run(["git", "-C", str(workspace), "remote", "add", "origin", str(bare)])

    # Local identity for the initial commit (also used by effect's _resolve_author).
    _run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"])
    _run(["git", "-C", str(workspace), "config", "user.name", "Test User"])

    # Seed the workspace with the fixture's vulnerable.py.
    shutil.copy(FIXTURE_DIR / "vulnerable.py", workspace / "vulnerable.py")
    _run(["git", "-C", str(workspace), "add", "vulnerable.py"])
    _run(["git", "-C", str(workspace), "commit", "-m", "initial"])
    sha = _run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture=True,
    ).strip()
    _run(["git", "-C", str(workspace), "push", "origin", "main"])

    return _Workspace(workspace=workspace, bare=bare, initial_commit_sha=sha)


def _install_gh_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh_stub = bin_dir / "gh"
    gh_stub.write_text(_GH_STUB, encoding="utf-8")
    gh_stub.chmod(0o755)
    existing_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + existing_path)
    return bin_dir


def _run(cmd: list[str], *, capture: bool = False) -> str:
    """Run a real subprocess; raise on failure. Strict — these are setup steps."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout if capture else ""


# --------------------------------------------------------------------------- fake sandbox


@dataclass
class _FakeSandbox:
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
        self.last_call = {"image": image, "cmd": cmd}
        if self.next_result is None:
            return RunResult(
                exit_code=0,
                stdout="ok\n",
                stderr="",
                timed_out=False,
                image=image,
                cmd=tuple(cmd),
            )
        return self.next_result


# --------------------------------------------------------------------------- case bootstrap


def _make_triage_packet(case_id: str) -> TriagePacket:
    return TriagePacket(
        case_id=case_id,
        summary="Path traversal in read_file via user-supplied filename",
        claim_type="path traversal",
        affected_components=["vulnerable.py::read_file"],
        confidence=0.9,
        disposition=TriageDisposition.ADVANCE,
        rationale="User-supplied filename passed directly to open() with no containment check.",
    )


def _make_patch_plan(case_id: str) -> PatchPlan:
    raw = json.loads((FIXTURE_DIR / "plan.json").read_text(encoding="utf-8"))
    raw["case_id"] = case_id
    return PatchPlan.model_validate(raw)


def _make_verdict() -> CrossCheckVerdict:
    return CrossCheckVerdict(
        vulnerability_summary="User input flows to open() without path containment.",
        fix_summary="Wraps the open() call with safe_path which enforces a base directory.",
        verdict="approve",
        reasoning="The fix directly closes the unsafe open() call with a path validator.",
        confidence=0.95,
    )


def _bootstrap_case_at_patch_applied(
    *,
    pw_root: Path,
    case_id: str,
) -> tuple[Case, TriagePacket, PatchPlan, CrossCheckVerdict]:
    """Open a case and walk it through the FSM to PATCH_APPLIED with all
    upstream artifacts in place. Uses journal.append() directly — every entry
    is real, chain-linked, and replay-validated."""
    packet = _make_triage_packet(case_id)
    plan = _make_patch_plan(case_id)
    verdict = _make_verdict()

    open_case(case_id=case_id, root=pw_root, raw_report=b"{}")

    paths = case_root_paths(pw_root, case_id)
    journal = Journal(paths["journal_dir"])
    store = ArtifactStore(paths["artifacts_dir"])

    def _append(from_state: str, to_state: str, artifact_bytes: list[tuple[bytes, str]]) -> None:
        case_now = replay(journal, store)
        assert case_now is not None
        artifact_refs: list[dict[str, Any]] = []
        for data, kind in artifact_bytes:
            sha = store.put(data)
            artifact_refs.append(Artifact(id=sha, kind=kind, size=len(data)).model_dump())
        journal.append(
            case_id=case_id,
            kind="transition",
            author="agent:bootstrap",
            payload={
                "from_state": from_state,
                "to_state": to_state,
                "reason": f"bootstrap {from_state}->{to_state}",
                "artifacts": artifact_refs,
            },
            prev_hash=case_now.last_hash,
            seq=case_now.last_seq + 1,
        )

    packet_bytes = canonical_json(packet.model_dump(mode="json"))
    plan_bytes = canonical_json(plan.model_dump(mode="json"))
    verdict_bytes = canonical_json(verdict.model_dump(mode="json"))

    _append(str(State.INTAKE), str(State.TRIAGED), [(packet_bytes, "triage_packet")])
    _append(str(State.TRIAGED), str(State.REPRODUCED), [])
    _append(str(State.REPRODUCED), str(State.PATCH_PROPOSED), [(plan_bytes, "patch_plan")])
    _append(
        str(State.PATCH_PROPOSED),
        str(State.PATCH_APPLIED),
        [(verdict_bytes, "cross_check_verdict")],
    )

    final = replay(journal, store)
    assert final is not None
    assert final.state == str(State.PATCH_APPLIED)
    return final, packet, plan, verdict


# --------------------------------------------------------------------------- drive helpers


def _build_registry(workspace: Path, case_root: Path) -> Registry:
    """Registry wired with the real PatchApplyAgent over a fake sandbox."""
    registry = Registry()
    registry.register(
        PatchApplyAgent(
            repo_root=workspace,
            sandbox=_FakeSandbox(),
            case_root=case_root,
            config=PatchwrightConfig(),
        )
    )
    return registry


def _build_effects() -> TransitionEffects:
    effects = TransitionEffects()
    register_default_effects(effects)
    return effects


def _drive(pw_root: Path, workspace: Path, case_root: Path, case_id: str) -> Case:
    return drive(
        case_id,
        _build_registry(workspace, case_root),
        pw_root,
        config=PatchwrightConfig(),
        effects=_build_effects(),
        workspace_root=workspace,
    )


def _journal_events(pw_root: Path, case_id: str) -> list[tuple[str, str | None]]:
    """List of (entry.kind, payload.kind-or-to_state) for compact assertions."""
    journal = Journal(case_root_paths(pw_root, case_id)["journal_dir"])
    events: list[tuple[str, str | None]] = []
    for entry in journal.read():
        if entry.kind == "transition":
            events.append((entry.kind, entry.payload.get("to_state")))
        elif entry.kind == "artifact_written":
            events.append((entry.kind, entry.payload.get("kind")))
        else:
            events.append((entry.kind, None))
    return events


# --------------------------------------------------------------------------- happy path


def test_e2e_happy_path_journals_full_pr_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-cwe22abc123"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)

    final = _drive(pw_root, ws.workspace, case_root, case_id)
    assert final.state == str(State.AWAITING_REVIEW)

    events = _journal_events(pw_root, case_id)
    # Full journal: case_opened + 4 bootstrap transitions + patch_apply transition
    # + 3 effect-runner artifact_written entries
    assert events == [
        ("case_opened", None),
        ("transition", str(State.TRIAGED)),
        ("transition", str(State.REPRODUCED)),
        ("transition", str(State.PATCH_PROPOSED)),
        ("transition", str(State.PATCH_APPLIED)),
        ("transition", str(State.AWAITING_REVIEW)),
        ("artifact_written", "branch_created"),
        ("artifact_written", "commit_pushed"),
        ("artifact_written", "pr_drafted"),
    ]


def test_e2e_pr_drafted_payload_carries_stub_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-payload123"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)
    _drive(pw_root, ws.workspace, case_root, case_id)

    journal = Journal(case_root_paths(pw_root, case_id)["journal_dir"])
    pr_drafted = next(
        e
        for e in journal.read()
        if e.kind == "artifact_written" and e.payload.get("kind") == "pr_drafted"
    )
    assert pr_drafted.payload["pr_number"] == 42
    assert pr_drafted.payload["pr_url"] == STUB_PR_URL
    assert pr_drafted.payload["base_branch"] == "main"
    assert pr_drafted.payload["branch"].startswith("patchwright/case-")


def test_e2e_branch_landed_on_remote_with_patched_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt-and-suspenders: the effect runner actually pushed a real commit to
    the bare remote, and the new branch contains the patched file."""
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-realbranch1"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)
    _drive(pw_root, ws.workspace, case_root, case_id)

    # List branches on the bare remote
    refs = _run(
        ["git", "-C", str(ws.bare), "for-each-ref", "--format=%(refname:short)"],
        capture=True,
    ).splitlines()
    pw_branches = [r for r in refs if r.startswith("patchwright/case-")]
    assert len(pw_branches) == 1

    # Verify the branch tip on the bare remote has the patched vulnerable.py
    show = _run(
        [
            "git",
            "-C",
            str(ws.bare),
            "show",
            f"{pw_branches[0]}:vulnerable.py",
        ],
        capture=True,
    )
    assert "patchwright_helpers" in show  # InsertImport landed
    assert "safe_path" in show  # WrapCallWithValidator landed


# --------------------------------------------------------------------------- negative: gh missing


def test_e2e_gh_missing_journals_pr_draft_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _init_git_workspace(tmp_path)
    # NO stub gh installed. Scrub PATH to a tmp dir containing ONLY a symlink to
    # `git` — homebrew installs git+gh in the same bin dir, so simply pointing
    # at git's parent doesn't remove gh.
    git_only = tmp_path / "git_only_bin"
    git_only.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None  # pytestmark already guards this
    (git_only / "git").symlink_to(real_git)
    monkeypatch.setenv("PATH", str(git_only))

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-noghbinary"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)
    final = _drive(pw_root, ws.workspace, case_root, case_id)

    # FSM still advanced to AWAITING_REVIEW (the agent succeeded; only the effect failed)
    assert final.state == str(State.AWAITING_REVIEW)

    # Effect journaled exactly one pr_draft_failed entry, step=is_available
    events = _journal_events(pw_root, case_id)
    assert events[-1] == ("artifact_written", "pr_draft_failed")
    journal = Journal(case_root_paths(pw_root, case_id)["journal_dir"])
    last = journal.read()[-1]
    assert last.payload["step"] == "is_available"


# --------------------------------------------------------------- negative: gh unauthenticated


def test_e2e_gh_unauthenticated_journals_pr_draft_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)
    monkeypatch.setenv("STUB_GH_AUTH_FAIL", "1")

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-noauth1234"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)
    final = _drive(pw_root, ws.workspace, case_root, case_id)

    assert final.state == str(State.AWAITING_REVIEW)
    journal = Journal(case_root_paths(pw_root, case_id)["journal_dir"])
    last = journal.read()[-1]
    assert last.kind == "artifact_written"
    assert last.payload["kind"] == "pr_draft_failed"
    assert last.payload["step"] == "is_available"


# --------------------------------------------------------------------------- negative: dirty tree


def test_e2e_dirty_tree_journals_pr_draft_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)

    # Introduce an uncommitted change that survives the agent run (the agent
    # never touches repo_root, so a pre-existing dirty file is still dirty when
    # the effect tries to create_branch).
    (ws.workspace / "stray.py").write_text("stray\n", encoding="utf-8")

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-dirty12345"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)
    final = _drive(pw_root, ws.workspace, case_root, case_id)

    assert final.state == str(State.AWAITING_REVIEW)
    journal = Journal(case_root_paths(pw_root, case_id)["journal_dir"])
    last = journal.read()[-1]
    assert last.kind == "artifact_written"
    assert last.payload["kind"] == "pr_draft_failed"
    assert last.payload["step"] == "create_branch"
    assert "uncommitted" in last.payload["reason"]


# --------------------------------------------------------------------- negative: branch exists


def test_e2e_branch_already_exists_journals_pr_draft_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-existingxx"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)

    # Pre-create the branch the effect runner is about to try to cut. branch_name
    # comes from PatchApplyAgent._branch_name(prefix, case_id) so we replicate
    # the shape locally to avoid coupling to internal helpers.
    short = case_id.removeprefix("case-")[:12]
    branch_name = f"patchwright/case-{short}"
    _run(["git", "-C", str(ws.workspace), "branch", branch_name])

    final = _drive(pw_root, ws.workspace, case_root, case_id)
    assert final.state == str(State.AWAITING_REVIEW)

    journal = Journal(case_root_paths(pw_root, case_id)["journal_dir"])
    last = journal.read()[-1]
    assert last.kind == "artifact_written"
    assert last.payload["kind"] == "pr_draft_failed"
    assert last.payload["step"] == "create_branch"
    assert "already exists" in last.payload["reason"]


# --------------------------------------------------------------------------- idempotency


def test_e2e_re_drive_adds_no_new_pr_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a happy run, re-running drive() must not produce duplicate PR
    entries. Case is at AWAITING_REVIEW; drive() finds no agent for that state
    and returns. (Idempotency at the effect level is exercised by the unit test
    in test_open_draft_pr_effect.py — this test guards the user-facing flow.)"""
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-idempot123"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)
    _drive(pw_root, ws.workspace, case_root, case_id)
    events_first = _journal_events(pw_root, case_id)

    _drive(pw_root, ws.workspace, case_root, case_id)
    events_second = _journal_events(pw_root, case_id)

    assert events_first == events_second
    pr_drafted_count = sum(1 for e in events_second if e == ("artifact_written", "pr_drafted"))
    assert pr_drafted_count == 1


# --------------------------------------------------------------------------- hash chain


def test_e2e_journal_hash_chain_intact_through_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Journal.read() validates the prev_hash chain on every read and raises
    ChainBroken if anything's off. Reading the post-effect journal end-to-end
    is the simplest possible assertion that the effect runner left the chain
    consistent."""
    ws = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)

    pw_root = tmp_path / "pw_root"
    case_root = tmp_path / "case_root"
    case_id = "case-chainxxxx1"

    _bootstrap_case_at_patch_applied(pw_root=pw_root, case_id=case_id)
    _drive(pw_root, ws.workspace, case_root, case_id)

    journal = Journal(case_root_paths(pw_root, case_id)["journal_dir"])
    entries = journal.read()
    # 1 case_opened + 4 bootstrap transitions + 1 patch_apply transition + 3 effect entries
    assert len(entries) == 9

    from itertools import pairwise  # noqa: PLC0415

    for prev, cur in pairwise(entries):
        assert cur.prev_hash == prev.content_hash
