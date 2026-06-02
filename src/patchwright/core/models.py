from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EntryKind = Literal[
    "case_opened",
    "transition",
    "artifact_written",
    "agent_invoked",
    "agent_rejected",
    "human_decision",
    "case_closed",
]


class Artifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Content-addressed id, format 'sha256:<hex>'.")
    kind: str = Field(description="Logical kind: 'raw_report', 'triage_packet', ...")
    media_type: str = "application/json"
    size: int = Field(ge=0)


class Transition(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    from_state: str
    to_state: str
    reason: str = ""


class JournalEntry(BaseModel):
    """Single immutable journal record.

    The hashed envelope is {seq, case_id, ts, kind, author, prev_hash, payload}.
    content_hash and signature are outside the hash so signatures can be added
    later (FR-PV-4, P2) without breaking the existing chain.
    """

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=0)
    case_id: str
    ts: str = Field(description="ISO-8601 UTC, microsecond precision, 'Z' suffix.")
    kind: EntryKind
    author: str = Field(description="'agent:<name>' | 'human:<id>' | 'system:orchestrator'")
    prev_hash: str
    payload: dict[str, Any]
    content_hash: str
    signature: str | None = None


class AgentResult(BaseModel):
    """What an agent returns. Agents never touch disk; the orchestrator owns I/O."""

    model_config = ConfigDict(frozen=True)

    transition: Transition
    new_artifacts: list[tuple[bytes, str]] = Field(
        default_factory=list,
        description="(raw_bytes, kind) tuples; orchestrator computes sha and writes.",
    )
    reason: str = ""


class Case(BaseModel):
    """Current case state, fully derivable by replaying the journal."""

    id: str
    state: str
    created_at: str
    artifacts: list[Artifact] = Field(default_factory=list)
    last_seq: int = -1
    last_hash: str
