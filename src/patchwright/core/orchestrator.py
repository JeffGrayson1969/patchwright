from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.errors import IllegalTransition, StaleAgent
from patchwright.core.fsm import INITIAL_STATE, TERMINAL_STATES, is_legal, is_terminal
from patchwright.core.hashing import GENESIS_HASH, sha256_b16
from patchwright.core.journal import Journal, now_iso
from patchwright.core.models import AgentResult, Artifact, Case, JournalEntry
from patchwright.core.registry import Registry

if TYPE_CHECKING:
    from patchwright.core.config import PatchwrightConfig

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- transition effects


@dataclass(frozen=True)
class EffectContext:
    """Read-only handle the orchestrator passes to a registered effect.

    Effects journal their own outcome via `journal.append(...)` — never raise.
    The orchestrator re-replays the case after the effect runs to pick up any
    journal entries the effect added (last_seq / last_hash advance).
    """

    case: Case
    store: ArtifactStore
    journal: Journal
    config: PatchwrightConfig
    workspace_root: Path


TransitionKey = tuple[str, str]
TransitionEffect = Callable[[EffectContext], None]


@dataclass
class TransitionEffects:
    """Registry of post-transition side-effects keyed on (from_state, to_state).

    Effects fire after a journaled transition + replay; they are the boundary
    between PatchWright's pure FSM and external-world side-effects (PR creation,
    notifications, etc.). Each effect is responsible for journaling its own
    success / failure via `EffectContext.journal`.

    An effect that raises is caught and logged — drive() never crashes because
    a side-effect had a coding bug.
    """

    _effects: dict[TransitionKey, list[TransitionEffect]] = field(default_factory=dict)

    def register(self, key: TransitionKey, fn: TransitionEffect) -> None:
        self._effects.setdefault(key, []).append(fn)

    def registered_for(self, key: TransitionKey) -> tuple[TransitionEffect, ...]:
        return tuple(self._effects.get(key, ()))

    def run(self, ctx: EffectContext, *, from_state: str) -> None:
        key = (from_state, ctx.case.state)
        for fn in self._effects.get(key, ()):
            try:
                fn(ctx)
            except Exception:
                log.exception(
                    "transition effect %r raised for key %s; effect is responsible "
                    "for journaling its own failure — continuing drive()",
                    getattr(fn, "__name__", repr(fn)),
                    key,
                )


class _Paths:
    def __init__(self, root: Path, case_id: str) -> None:
        self.root = root
        self.case_id = case_id
        self.journal_dir = root / "journal" / case_id
        self.artifacts_dir = root / "artifacts"


# --------------------------------------------------------------------------- replay


def _apply_entry(case: Case | None, entry: JournalEntry, _store: ArtifactStore) -> Case:
    """Pure reducer: (Case, entry) -> Case. Updated case.last_seq/last_hash always."""
    if entry.kind == "case_opened":
        if case is not None:
            raise ValueError(f"duplicate case_opened for case_id {entry.case_id}")
        initial_artifacts: list[Artifact] = []
        raw_report_payload = entry.payload.get("raw_report")
        if raw_report_payload is not None:
            initial_artifacts.append(Artifact.model_validate(raw_report_payload))
        return Case(
            id=entry.case_id,
            state=entry.payload["initial_state"],
            created_at=entry.payload["created_at"],
            artifacts=initial_artifacts,
            last_seq=entry.seq,
            last_hash=entry.content_hash,
        )

    if case is None:
        raise ValueError(f"entry {entry.kind} before case_opened for {entry.case_id}")

    if entry.kind == "transition":
        artifact_refs = [Artifact.model_validate(a) for a in entry.payload.get("artifacts", [])]
        return case.model_copy(
            update={
                "state": entry.payload["to_state"],
                "artifacts": case.artifacts + artifact_refs,
                "last_seq": entry.seq,
                "last_hash": entry.content_hash,
            }
        )

    if entry.kind == "agent_rejected":
        return case.model_copy(update={"last_seq": entry.seq, "last_hash": entry.content_hash})

    # case_closed, artifact_written, agent_invoked, human_decision are accepted as
    # bookkeeping events in P0; the only state-affecting kinds are case_opened and transition.
    return case.model_copy(update={"last_seq": entry.seq, "last_hash": entry.content_hash})


def replay(journal: Journal, store: ArtifactStore) -> Case | None:
    """Rebuild Case state by replaying the journal. Returns None if no case_opened yet."""
    case: Case | None = None
    for entry in journal.read():
        case = _apply_entry(case, entry, store)
    return case


# --------------------------------------------------------------------------- bootstrap


def open_case(
    *,
    case_id: str,
    root: Path,
    raw_report: bytes,
    raw_report_kind: str = "raw_report",
    raw_report_media_type: str = "application/json",
) -> Case:
    """Open a new case: write the raw report artifact, append case_opened entry.

    Idempotent — if the journal already has a case_opened entry, returns the
    replayed Case unchanged.
    """
    paths = _Paths(root, case_id)
    journal = Journal(paths.journal_dir)
    store = ArtifactStore(paths.artifacts_dir)

    existing = replay(journal, store)
    if existing is not None:
        return existing

    raw_sha = store.put(raw_report)
    raw_artifact = Artifact(
        id=raw_sha,
        kind=raw_report_kind,
        media_type=raw_report_media_type,
        size=len(raw_report),
    )

    entry = journal.append(
        case_id=case_id,
        kind="case_opened",
        author="system:orchestrator",
        payload={
            "initial_state": str(INITIAL_STATE),
            "created_at": now_iso(),
            "raw_report": raw_artifact.model_dump(),
        },
        prev_hash=GENESIS_HASH,
        seq=0,
    )
    case = _apply_entry(None, entry, store)
    return case


# --------------------------------------------------------------------------- drive


def drive(
    case_id: str,
    registry: Registry,
    root: Path,
    *,
    config: PatchwrightConfig | None = None,
    effects: TransitionEffects | None = None,
    workspace_root: Path | None = None,
) -> Case:
    """Run agents on the case until terminal or no agent for the current state.

    Replay-after-every-transition: NFR-R-1/R-2 become runtime invariants.

    Optional kwargs are for post-transition side-effects (AEG-425, M2-pr.5).
    All three must be present for effects to fire; otherwise the behavior is
    identical to P0 (no effects). This preserves back-compat for every existing
    caller and test.
    """
    paths = _Paths(root, case_id)
    journal = Journal(paths.journal_dir)
    store = ArtifactStore(paths.artifacts_dir)

    case = replay(journal, store)
    if case is None:
        raise ValueError(f"case {case_id} has not been opened")

    effects_armed = effects is not None and config is not None and workspace_root is not None

    while case.state not in {str(s) for s in TERMINAL_STATES}:
        agent = registry.agent_for_state(case.state)
        if agent is None:
            log.info("no agent for state %s; pausing (human-required)", case.state)
            return case

        result: AgentResult = agent(case, store.read_only())
        trans = result.transition

        if trans.case_id != case.id:
            _append_rejection(journal, case, "case_id mismatch", agent.name)
            raise StaleAgent(
                f"agent {agent.name} returned case_id {trans.case_id!r} for {case.id!r}"
            )

        if trans.from_state != case.state:
            _append_rejection(journal, case, "from_state mismatch", agent.name)
            raise StaleAgent(
                f"agent {agent.name} proposed from_state {trans.from_state!r} "
                f"but case is in {case.state!r}"
            )

        if not is_legal(case.state, trans.to_state):
            _append_rejection(
                journal,
                case,
                f"illegal transition {case.state} -> {trans.to_state}",
                agent.name,
            )
            raise IllegalTransition(
                f"agent {agent.name} proposed illegal transition {case.state} -> {trans.to_state}"
            )

        artifact_refs = _persist_artifacts(store, result.new_artifacts)

        journal.append(
            case_id=case.id,
            kind="transition",
            author=f"agent:{agent.name}",
            payload={
                "from_state": case.state,
                "to_state": trans.to_state,
                "reason": trans.reason or result.reason,
                "artifacts": [a.model_dump() for a in artifact_refs],
            },
            prev_hash=case.last_hash,
            seq=case.last_seq + 1,
        )

        # Replay-after-transition: cheap at P0 cardinality, executable invariant.
        case = replay(journal, store)
        if case is None:  # pragma: no cover - impossible if append above succeeded
            raise RuntimeError("replay returned None after appending transition")

        # Post-transition side-effects (AEG-425). Effects journal their own outcome;
        # they MAY append entries but MUST NOT raise. We re-replay to pick up any
        # last_seq / last_hash advancement so the next agent iteration uses correct
        # prev_hash.
        if effects_armed:
            assert effects is not None and config is not None and workspace_root is not None
            ctx = EffectContext(
                case=case,
                store=store,
                journal=journal,
                config=config,
                workspace_root=workspace_root,
            )
            effects.run(ctx, from_state=trans.from_state)
            case = replay(journal, store)
            if case is None:  # pragma: no cover - impossible after journal was non-empty
                raise RuntimeError("replay returned None after running effects")

        if is_terminal(case.state):
            journal.append(
                case_id=case.id,
                kind="case_closed",
                author="system:orchestrator",
                payload={"terminal_state": case.state},
                prev_hash=case.last_hash,
                seq=case.last_seq + 1,
            )
            case = replay(journal, store)
            assert case is not None

    return case


def _persist_artifacts(
    store: ArtifactStore, new_artifacts: list[tuple[bytes, str]]
) -> list[Artifact]:
    out: list[Artifact] = []
    for data, kind in new_artifacts:
        sha = store.put(data)
        out.append(Artifact(id=sha, kind=kind, media_type="application/json", size=len(data)))
    return out


def _append_rejection(journal: Journal, case: Case, reason: str, agent_name: str) -> None:
    journal.append(
        case_id=case.id,
        kind="agent_rejected",
        author=f"agent:{agent_name}",
        payload={"reason": reason, "case_state": case.state},
        prev_hash=case.last_hash,
        seq=case.last_seq + 1,
    )


# --------------------------------------------------------------------------- helpers


def case_root_paths(root: Path, case_id: str) -> dict[str, Path]:
    """Public helper for tests / CLI to introspect on-disk layout."""
    p = _Paths(root, case_id)
    return {
        "journal_dir": p.journal_dir,
        "journal_file": p.journal_dir / Journal.JOURNAL_FILENAME,
        "artifacts_dir": p.artifacts_dir,
    }


def stable_case_id(seed: bytes) -> str:
    """Deterministic case id from a seed (for tests and the hello demo)."""
    return "case-" + sha256_b16(seed)[7:19]


__all__ = [
    "EffectContext",
    "TransitionEffect",
    "TransitionEffects",
    "TransitionKey",
    "case_root_paths",
    "drive",
    "open_case",
    "replay",
    "stable_case_id",
]
