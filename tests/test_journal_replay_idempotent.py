"""Test #2 — replay idempotence.

After driving a case to DONE, replaying the journal produces a deep-equal
Case. Running drive() again appends no new entries and the last_hash is
unchanged.
"""

from __future__ import annotations

from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.journal import Journal
from patchwright.core.orchestrator import case_root_paths, drive, open_case, replay
from patchwright.core.registry import default_registry


def _run_to_terminal(root: Path) -> tuple[str, Journal, ArtifactStore]:
    case_id = "case-replay-test"
    open_case(case_id=case_id, root=root, raw_report=b'{"id":"X"}')
    drive(case_id, default_registry(), root)
    paths = case_root_paths(root, case_id)
    return case_id, Journal(paths["journal_dir"]), ArtifactStore(paths["artifacts_dir"])


_TERMINAL_STATE = "REJECTED"  # noop_closer emits TRIAGED->REJECTED (TRIAGED->DONE removed)


def test_replay_produces_deep_equal_case(tmp_path: Path) -> None:
    _, journal, store = _run_to_terminal(tmp_path)
    c1 = replay(journal, store)
    c2 = replay(journal, store)
    assert c1 is not None
    assert c2 is not None
    assert c1.model_dump() == c2.model_dump()
    assert c1.state == _TERMINAL_STATE


def test_rerunning_drive_appends_no_new_entries(tmp_path: Path) -> None:
    case_id, journal, _ = _run_to_terminal(tmp_path)
    entries_before = journal.read()
    case_after = drive(case_id, default_registry(), tmp_path)
    entries_after = journal.read()

    assert len(entries_before) == len(entries_after)
    assert entries_before[-1].content_hash == entries_after[-1].content_hash
    assert case_after.last_hash == entries_before[-1].content_hash
    assert case_after.state == _TERMINAL_STATE
