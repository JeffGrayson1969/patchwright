"""Integration: MCP apply_patch drives PATCH_PROPOSED -> AWAITING_REVIEW (AEG-544).

Proves the AEG-379 exit criterion for the last hop: an MCP host calling
apply_patch runs the cross-checker gate -> codemod + tests -> draft-PR effect,
ending at AWAITING_REVIEW. Real git (tmp workspace + bare remote), a stub `gh`,
a fake sandbox (tests pass), and a fake cross-checker provider (approve verdict)
— no network / LLM / docker. Skipped when git is missing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.config import PatchwrightConfig
from patchwright.core.fsm import State
from patchwright.core.hashing import canonical_json
from patchwright.core.journal import Journal
from patchwright.core.models import Artifact
from patchwright.core.orchestrator import case_root_paths, open_case, replay
from patchwright.core.reviews import record_human_decision
from patchwright.core.sandbox import Mount, NetworkPolicy, ResourceLimits, RunResult
from patchwright.mcp_server import tools
from patchwright.models.cross_check import CrossCheckVerdict
from patchwright.models.patch_plan import PatchPlan
from patchwright.models.triage import TriageDisposition, TriagePacket

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "patch_corpus" / "cwe22_path_traversal"
STUB_PR_URL = "https://github.com/test-owner/test-repo/pull/42"
STUB_REPO_NAME = "test-owner/test-repo"

_GH_STUB = textwrap.dedent(
    f"""\
    #!/usr/bin/env python3
    import sys
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print("logged in"); sys.exit(0)
    if args[:2] == ["repo", "view"]:
        print("{STUB_REPO_NAME}"); sys.exit(0)
    if args[:2] == ["pr", "create"]:
        print("{STUB_PR_URL}"); sys.exit(0)
    sys.stderr.write(f"stub gh: unrecognized argv: {{args}}\\n"); sys.exit(2)
    """
)


def _run(cmd: list[str], *, capture: bool = False) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    return r.stdout if capture else ""


def _init_git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    bare = tmp_path / "remote.git"
    bare.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--bare", "--initial-branch=main", str(bare)])
    _run(["git", "init", "--initial-branch=main", str(workspace)])
    _run(["git", "-C", str(workspace), "remote", "add", "origin", str(bare)])
    _run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"])
    _run(["git", "-C", str(workspace), "config", "user.name", "Test User"])
    shutil.copy(FIXTURE_DIR / "vulnerable.py", workspace / "vulnerable.py")
    _run(["git", "-C", str(workspace), "add", "vulnerable.py"])
    _run(["git", "-C", str(workspace), "commit", "-m", "initial"])
    _run(["git", "-C", str(workspace), "push", "origin", "main"])
    return workspace


def _install_gh_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(_GH_STUB, encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))


@dataclass
class _FakeSandbox:
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
        return RunResult(
            exit_code=0, stdout="ok\n", stderr="", timed_out=False, image=image, cmd=tuple(cmd)
        )


@dataclass
class _FakeCrossChecker:
    name: str = "fake-cross"
    model: str = "fake"

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: object = None,
        max_output_tokens: int = 8192,
    ) -> CrossCheckVerdict:
        return CrossCheckVerdict(
            vulnerability_summary="User input flows to open() without containment.",
            fix_summary="Wraps open() with a base-dir path validator.",
            verdict="approve",
            reasoning="The fix closes the unsafe open() call.",
            confidence=0.95,
        )


def _seed_patch_proposed(pw_root: Path, case_id: str) -> None:
    """Walk a fixture case to PATCH_PROPOSED with triage_packet + patch_plan artifacts."""
    packet = TriagePacket(
        case_id=case_id,
        summary="Path traversal in read_file",
        claim_type="path traversal",
        affected_components=["vulnerable.py::read_file"],
        confidence=0.9,
        disposition=TriageDisposition.ADVANCE,
        rationale="User filename passed to open() with no containment.",
    )
    raw = json.loads((FIXTURE_DIR / "plan.json").read_text(encoding="utf-8"))
    raw["case_id"] = case_id
    plan = PatchPlan.model_validate(raw)

    open_case(case_id=case_id, root=pw_root, raw_report=b"{}")
    paths = case_root_paths(pw_root, case_id)
    journal = Journal(paths["journal_dir"])
    store = ArtifactStore(paths["artifacts_dir"])

    def _append(frm: str, to: str, arts: list[tuple[bytes, str]]) -> None:
        now = replay(journal, store)
        assert now is not None
        refs = [Artifact(id=store.put(d), kind=k, size=len(d)).model_dump() for d, k in arts]
        journal.append(
            case_id=case_id,
            kind="transition",
            author="agent:bootstrap",
            payload={"from_state": frm, "to_state": to, "reason": "seed", "artifacts": refs},
            prev_hash=now.last_hash,
            seq=now.last_seq + 1,
        )

    _append(
        str(State.INTAKE),
        str(State.TRIAGED),
        [(canonical_json(packet.model_dump(mode="json")), "triage_packet")],
    )
    _append(str(State.TRIAGED), str(State.REPRODUCED), [])
    _append(
        str(State.REPRODUCED),
        str(State.PATCH_PROPOSED),
        [(canonical_json(plan.model_dump(mode="json")), "patch_plan")],
    )
    assert replay(journal, store).state == str(State.PATCH_PROPOSED)  # type: ignore[union-attr]

    # Operator approval on record — apply_patch's per-case gate (CLAUDE.md #8).
    record_human_decision(
        case_id=case_id, root=pw_root, decision="approve", reason="looks right", identity="operator"
    )


def test_apply_patch_drives_to_awaiting_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _init_git_workspace(tmp_path)
    _install_gh_stub(tmp_path, monkeypatch)
    monkeypatch.setattr(tools, "_sandbox_from_config", lambda config: _FakeSandbox())
    monkeypatch.setattr(
        "patchwright.providers.factory.build_cross_checker", lambda config: _FakeCrossChecker()
    )

    pw_root = tmp_path / "cases"
    case_id = "case-applye2e01"
    _seed_patch_proposed(pw_root, case_id)

    result = tools.apply_patch(
        root=pw_root,
        config=PatchwrightConfig(),
        case_id=case_id,
        workspace_root=str(workspace),
        allow_mutations=True,
    )

    assert result["ok"] is True, result
    assert result["state"] == str(State.AWAITING_REVIEW)

    # The draft-PR effect ran and journaled its outcome.
    entries = Journal(case_root_paths(pw_root, case_id)["journal_dir"]).read()
    to_states = [e.payload.get("to_state") for e in entries if e.kind == "transition"]
    effect_markers = [e.payload.get("kind") for e in entries if e.kind == "artifact_written"]
    assert str(State.PATCH_APPLIED) in to_states
    assert str(State.AWAITING_REVIEW) in to_states
    assert "pr_drafted" in effect_markers, effect_markers
