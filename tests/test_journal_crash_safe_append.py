"""Test #3 — crash-safe append.

Invariant: a torn trailing line on disk is tolerated on replay (truncated),
and the next append uses the prev_hash of the last *valid* entry.
"""

from __future__ import annotations

from pathlib import Path

from patchwright.core.journal import Journal


def _write_two_entries(j: Journal) -> tuple[str, str]:
    e1 = j.append(
        case_id="case-x",
        kind="case_opened",
        author="system:orchestrator",
        payload={"initial_state": "INTAKE", "created_at": "2026-06-02T12:00:00.000000Z"},
        prev_hash="sha256:" + "0" * 64,
        seq=0,
    )
    e2 = j.append(
        case_id="case-x",
        kind="transition",
        author="agent:noop_triage",
        payload={"from_state": "INTAKE", "to_state": "TRIAGED", "reason": "noop", "artifacts": []},
        prev_hash=e1.content_hash,
        seq=1,
    )
    return e1.content_hash, e2.content_hash


def test_torn_tail_is_truncated_on_read(tmp_path: Path) -> None:
    j = Journal(tmp_path)
    h1, h2 = _write_two_entries(j)
    # Append a deliberate torn line: half a JSON object, no newline.
    with open(j.path, "ab") as f:
        f.write(b'{"seq":2,"case_id":"case-x"')

    entries = j.read()
    assert len(entries) == 2
    assert entries[0].content_hash == h1
    assert entries[1].content_hash == h2
    # Truncation persists: the corrupt suffix was rewritten away.
    assert j.path.read_bytes().count(b"\n") == 2


def test_next_append_uses_last_valid_prev_hash(tmp_path: Path) -> None:
    j = Journal(tmp_path)
    _, h2 = _write_two_entries(j)
    with open(j.path, "ab") as f:
        f.write(b'{"seq":2,"case_id"')

    # Read triggers torn-tail truncation.
    entries = j.read()
    assert len(entries) == 2

    e3 = j.append(
        case_id="case-x",
        kind="case_closed",
        author="system:orchestrator",
        payload={"terminal_state": "DONE"},
        prev_hash=h2,
        seq=2,
    )
    assert e3.prev_hash == h2
    assert e3.seq == 2

    # Full read still validates the chain end-to-end.
    final = j.read()
    assert [e.seq for e in final] == [0, 1, 2]
    assert final[-1].content_hash == e3.content_hash
