"""TriagePacket — structured output of the triage agent (FR-TR-3).

The triage agent calls the LLM with a delimiter-wrapped report and the LLM
returns a TriagePacket. The orchestrator stores this as an artifact in the
journal; the human review CLI (M4) renders it.

Schema constraints (PRD §6.2):
- FR-TR-2: reporter trust is rule-based; LLM does NOT score it.
- FR-TR-3: structured packet with dedup result, summary, confidence,
  suggested disposition.
- FR-TR-4 (P2): AI-slop detection. Not in P1.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TriageDisposition(StrEnum):
    """What the agent recommends. Human still approves (no auto-advance in v1)."""

    ADVANCE = "advance"
    """Looks like a real, novel report — advance to REPRODUCE."""

    REJECT_DUPLICATE = "reject_duplicate"
    """Already tracked under another case or upstream CVE."""

    REJECT_OUT_OF_SCOPE = "reject_out_of_scope"
    """Not a vulnerability in this project (wrong repo, feature request, etc.)."""

    REJECT_LOW_QUALITY = "reject_low_quality"
    """Insufficient detail to act on; agent could not extract a vuln claim."""

    REQUEST_INFO = "request_info"
    """Plausible but underspecified — ask the reporter for more detail."""


class DedupMatch(BaseModel):
    """One potential prior match found during semantic dedup (FR-TR-1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identifier: str = Field(
        description="Existing case id, CVE id, or GHSA id (e.g. 'CVE-2024-1234')."
    )
    similarity: float = Field(
        ge=0.0, le=1.0, description="Estimated semantic similarity, 0.0 to 1.0."
    )
    rationale: str = Field(description="One-sentence explanation of why this matches.")


class TriagePacket(BaseModel):
    """Structured triage output. Stored as a journal artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"

    case_id: str = Field(description="Case id this packet describes.")

    summary: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Plain-English summary of the claimed vulnerability. Suitable for a "
            "human reviewer to scan in seconds."
        ),
    )

    claim_type: str = Field(
        max_length=200,
        description=(
            "Short label for the claimed vulnerability class — e.g. 'path traversal', "
            "'SQL injection', 'deserialization', 'unspecified'."
        ),
    )

    affected_components: list[str] = Field(
        default_factory=list,
        description="File paths or module names the report points at; empty if unspecified.",
    )

    dedup_matches: list[DedupMatch] = Field(
        default_factory=list,
        description="Possible prior matches against OSV/GHSA/open cases. Empty if novel.",
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Agent confidence that the report describes a real vulnerability.",
    )

    disposition: TriageDisposition = Field(
        description="Suggested action. Human approves before any state transition."
    )

    rationale: str = Field(
        min_length=1,
        max_length=4000,
        description="Why the agent chose this disposition. Cite specific evidence.",
    )
