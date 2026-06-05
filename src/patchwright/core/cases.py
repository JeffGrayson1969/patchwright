"""Case enumeration + lookup helpers.

The journal-as-state-store invariant means case state is reconstructed from
the on-disk journal. This module wraps that for clients that need to enumerate
cases without driving the FSM (list, explain, review).

Layout (set by orchestrator.case_root_paths):
  <root>/journal/<case_id>/journal.jsonl
  <root>/artifacts/<sha>.bin
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.journal import Journal
from patchwright.core.models import Case, JournalEntry
from patchwright.core.orchestrator import case_root_paths, replay


@dataclass(frozen=True)
class CaseRecord:
    """One enumerated case with its replayed state and full journal history."""

    case: Case
    entries: list[JournalEntry]


def list_case_ids(root: Path) -> list[str]:
    """Enumerate case ids on disk (directory names under <root>/journal/)."""
    journal_root = root / "journal"
    if not journal_root.is_dir():
        return []
    return sorted(d.name for d in journal_root.iterdir() if d.is_dir())


def load_case(case_id: str, root: Path) -> CaseRecord:
    """Replay one case from disk. Raises FileNotFoundError if the case is
    missing or has no journal entries."""
    paths = case_root_paths(root, case_id)
    journal = Journal(paths["journal_dir"])
    store = ArtifactStore(paths["artifacts_dir"])

    case = replay(journal, store)
    if case is None:
        raise FileNotFoundError(f"case {case_id!r} not found under {root}")

    return CaseRecord(case=case, entries=journal.read())


def list_all_cases(root: Path) -> list[CaseRecord]:
    """Load every case under root. Skips empty/corrupt directories silently —
    callers that need errors should call load_case() per id."""
    out: list[CaseRecord] = []
    for case_id in list_case_ids(root):
        try:
            out.append(load_case(case_id, root))
        except Exception:
            # Tolerate junk dirs without crashing the list command.
            continue
    return out


__all__ = ["CaseRecord", "list_all_cases", "list_case_ids", "load_case"]
