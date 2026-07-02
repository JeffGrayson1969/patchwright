"""`patchwright list` — show cases on disk, filterable by state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from patchwright.core.cases import CaseRecord, list_all_cases
from patchwright.core.journal_crypto import cipher_for_reading


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "list",
        help="List PatchWright cases under a root.",
        description="One row per case: id, state, age, last entry kind, last author.",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Persistence root containing journal/ and artifacts/ dirs. Defaults to cwd.",
    )
    p.add_argument(
        "--state",
        type=str,
        default=None,
        help="Only show cases in this FSM state (e.g. INTAKE, TRIAGED, DONE).",
    )
    p.set_defaults(func=cmd_list)


def cmd_list(args: argparse.Namespace) -> int:
    root: Path = args.root or Path.cwd()
    cases = list_all_cases(root, cipher=cipher_for_reading())
    if args.state:
        cases = [c for c in cases if c.case.state == args.state]

    if not cases:
        print(f"no cases found under {root}", file=sys.stderr)
        return 0

    _print_table(cases)
    return 0


def _print_table(cases: list[CaseRecord]) -> None:
    headers = ("CASE", "STATE", "ENTRIES", "LAST_KIND", "LAST_AUTHOR")
    rows = [
        (
            c.case.id,
            c.case.state,
            str(len(c.entries)),
            c.entries[-1].kind if c.entries else "",
            c.entries[-1].author if c.entries else "",
        )
        for c in cases
    ]
    widths = [max(len(r[i]) for r in (headers, *rows)) for i in range(len(headers))]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    print(line(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))
