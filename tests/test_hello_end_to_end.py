"""Test #5 — hello-world end-to-end.

Drives the CLI hello command and validates: terminal state, expected entry
sequence, all content_hashes verify, Merkle chain unbroken from genesis.
"""

from __future__ import annotations

from pathlib import Path

from patchwright.cli.__main__ import main as cli_main
from patchwright.cli.hello import _load_fixture
from patchwright.core.hashing import GENESIS_HASH
from patchwright.core.journal import Journal, verify_entry_hash
from patchwright.core.orchestrator import case_root_paths, stable_case_id

EXPECTED_KIND_SEQUENCE = [
    "case_opened",
    "transition",
    "transition",
    "case_closed",
]


def test_hello_end_to_end_via_cli(tmp_path: Path, capsys: object) -> None:
    fixture = _load_fixture()
    case_id = stable_case_id(fixture)

    rc1 = cli_main(["hello", "--root", str(tmp_path)])
    assert rc1 == 0

    # Second invocation should detect idempotent replay and not append entries.
    rc2 = cli_main(["hello", "--root", str(tmp_path)])
    assert rc2 == 0

    journal = Journal(case_root_paths(tmp_path, case_id)["journal_dir"])
    entries = journal.read()
    assert [e.kind for e in entries] == EXPECTED_KIND_SEQUENCE

    # Chain unbroken from genesis.
    prev = GENESIS_HASH
    for i, e in enumerate(entries):
        assert e.seq == i
        assert e.prev_hash == prev
        assert verify_entry_hash(e)
        prev = e.content_hash

    # Transitions show the expected to_states in order.
    transitions = [e for e in entries if e.kind == "transition"]
    assert [t.payload["to_state"] for t in transitions] == ["TRIAGED", "DONE"]
