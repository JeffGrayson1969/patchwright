"""`patchwright hello` — P0 end-to-end demo.

Steps:
  1. Open a case using the bundled fixture report.
  2. drive() the FSM through INTAKE -> TRIAGED -> DONE via noop_triage + noop_closer.
  3. Print every journal entry as pretty JSON.
  4. On a second invocation against the same --root, demonstrate replay
     idempotence: no new entries, same last_hash.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from importlib import resources
from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.journal import Journal
from patchwright.core.orchestrator import (
    case_root_paths,
    drive,
    open_case,
    replay,
    stable_case_id,
)
from patchwright.core.registry import default_registry

FIXTURE_NAME = "hello_report.json"


def _load_fixture() -> bytes:
    return resources.files("patchwright.fixtures").joinpath(FIXTURE_NAME).read_bytes()


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "hello",
        help="Run the P0 end-to-end demo case through the FSM.",
        description=(
            "Ingests a bundled fixture report, drives it INTAKE -> TRIAGED -> DONE, "
            "prints the journal, and demonstrates replay idempotence on a second run."
        ),
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Persistence root. Default: a fresh tempdir (single-shot demo).",
    )
    p.add_argument(
        "--case-id",
        type=str,
        default=None,
        help="Stable case id. Default: deterministic from fixture sha.",
    )
    p.set_defaults(func=cmd_hello)


def cmd_hello(args: argparse.Namespace) -> int:
    fixture = _load_fixture()
    root: Path = args.root or Path(tempfile.mkdtemp(prefix="pw-hello-"))
    case_id: str = args.case_id or stable_case_id(fixture)

    paths = case_root_paths(root, case_id)
    journal = Journal(paths["journal_dir"])
    store = ArtifactStore(paths["artifacts_dir"])

    pre_existing = replay(journal, store)
    pre_last_hash = pre_existing.last_hash if pre_existing is not None else None
    pre_seq = pre_existing.last_seq if pre_existing is not None else -1

    print(f"root      : {root}", file=sys.stderr)
    print(f"case_id   : {case_id}", file=sys.stderr)

    open_case(case_id=case_id, root=root, raw_report=fixture, raw_report_kind="raw_report")
    case = drive(case_id=case_id, registry=default_registry(), root=root)

    entries = journal.read()
    print(json.dumps({"final_state": case.state, "last_seq": case.last_seq}, indent=2))
    print(json.dumps([_pretty(e) for e in entries], indent=2))

    if pre_existing is not None and case.last_hash == pre_last_hash and case.last_seq == pre_seq:
        print("replay produced identical state, last_hash unchanged", file=sys.stderr)

    return 0


def _pretty(entry: object) -> dict[str, object]:
    payload = json.loads(entry.model_dump_json())  # type: ignore[attr-defined]
    assert isinstance(payload, dict)
    return payload
