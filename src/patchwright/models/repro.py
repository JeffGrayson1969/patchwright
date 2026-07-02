"""ReproLog — artifact emitted by the reproduce agent (AEG-462).

Captures the RunResult of the sandboxed PoC execution verbatim plus the
agent's verdict. Stored as kind="repro_log"; read by cli/review.py to
render the human evidence pack and by downstream agents (patch_plan)
that need to know what the PoC actually did.

Stdio is tail-trimmed at 4 KiB each — matching `TestResult` in
`patch_apply_result.py` — so the journal stays small even when a
PoC produces megabytes of output.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReproVerdict = Literal["reproduced", "not_reproducible"]


class ReproLog(BaseModel):
    """Outcome of one reproduce-agent invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"

    case_id: str

    poc_artifact_id: str | None = Field(
        default=None,
        description=(
            "The PocSpec artifact this log was produced from. None when the "
            "case had no PocSpec at all (NOT_REPRODUCIBLE / no_poc reason)."
        ),
    )

    verdict: ReproVerdict
    reason: str = Field(
        description="Short human-readable explanation of the verdict.",
    )

    image: str = Field(
        description="The image the PoC actually ran in (after PocSpec or config fallback).",
    )

    cmd: tuple[str, ...] = Field(
        default=(),
        description="argv as executed. Empty when no PocSpec was present.",
    )

    exit_code: int = Field(
        default=-1,
        description="Sandbox exit code. -1 when the sandbox couldn't run or no PoC was present.",
    )

    stdout_tail: str = Field(max_length=4096, default="")
    stderr_tail: str = Field(max_length=4096, default="")

    timed_out: bool = False
    network_enabled: bool = False

    sandbox_name: str = Field(
        description="Name of the SandboxRunner that ran the PoC (e.g. 'gvisor', 'docker').",
    )
