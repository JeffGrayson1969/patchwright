"""PatchApplyResult — artifact emitted by the patch_apply agent (AEG-424).

The post-transition effect runner (AEG-425) reads this artifact to drive
the GitHub PR creation. Three fields couple the agent to the effect
runner:
  - scratch_dir: the materialized worktree the effect runner reads
  - branch_name: the feature branch to push
  - commit_message: the commit body to use for the patch

scratch_dir is intentionally a path (not content-addressed) because the
worktree is an ephemeral, mutable on-disk artifact the effect runner
consumes once and then deletes. Everything else is content-addressable.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class TestResult(BaseModel):
    """Outcome of running `conventions.test_command` inside the SandboxRunner
    over the patched scratch worktree. exit_code != 0 OR timed_out=True both
    route the FSM to REJECTED."""

    # Tell pytest this is a Pydantic model, not a test class.
    __test__: ClassVar[bool] = False

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int
    stdout_tail: str = Field(max_length=4096, default="")
    stderr_tail: str = Field(max_length=4096, default="")
    timed_out: bool = False


class PatchApplyResult(BaseModel):
    """Artifact emitted by the patch_apply agent. Read by AEG-425 to open
    the draft PR; read by cli/review.py to render the human evidence pack."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"

    case_id: str
    plan_artifact_id: str = Field(
        description="The PatchPlan artifact this result was materialized from. 'sha256:<hex>'."
    )

    modified_files: tuple[str, ...] = Field(
        description="Repo-relative POSIX paths the codemod (+ test gen) produced. Sorted."
    )

    diff: str = Field(
        description="Unified diff of the patch. The same text shown in cli/review.py."
    )

    test_result: TestResult

    scratch_dir: str = Field(
        description=(
            "Absolute path to the materialized worktree the effect runner reads "
            "to drive `git commit`. Ephemeral; deleted after pr_drafted succeeds."
        )
    )

    branch_name: str = Field(
        description=(
            "Feature branch the effect runner will create and push, e.g. 'patchwright/case-abc123'."
        )
    )

    base_branch: str = Field(
        description="Base branch the draft PR is opened against (typically 'main')."
    )

    commit_message: str = Field(
        description="Full commit message body (summary + rationale, conventional-commits shape)."
    )
