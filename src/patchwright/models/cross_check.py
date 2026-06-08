"""CrossCheckVerdict — the output schema for the cross_checker agent (M2.5, T9 mitigation)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CrossCheckVerdict(BaseModel):
    """Pydantic-validated output of the cross-checker LLM call.

    The cross-checker reads the original report + candidate PatchPlan and judges
    whether the fix actually addresses the reported vulnerability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    vulnerability_summary: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "The cross-checker's independent summary of what vulnerability "
            "the original report describes."
        ),
    )

    fix_summary: str = Field(
        min_length=1,
        max_length=2000,
        description="The cross-checker's independent summary of what the candidate PatchPlan does.",
    )

    verdict: Literal["approve", "refuse"] = Field(
        description=(
            "'approve' if the fix addresses the vulnerability; "
            "'refuse' if intent diverges or the plan is unsafe."
        ),
    )

    reasoning: str = Field(
        min_length=1,
        max_length=4000,
        description="Explanation of the verdict. Read by the human reviewer.",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Cross-checker's confidence in its verdict (0.0 = no evidence, 1.0 = certain).",
    )
