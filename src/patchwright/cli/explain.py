"""`patchwright explain` — print the markdown evidence packet for a case."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.cases import load_case
from patchwright.core.evidence import render
from patchwright.core.orchestrator import case_root_paths


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "explain",
        help="Render the markdown evidence packet for a case to stdout.",
        description=(
            "Shows case header, origin, timeline, attached artifacts, and the "
            "reasoning trace collected from transition reasons. Same content "
            "that `patchwright review` opens in $EDITOR."
        ),
    )
    p.add_argument("case_id", type=str)
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Persistence root. Defaults to cwd.",
    )
    p.set_defaults(func=cmd_explain)


def cmd_explain(args: argparse.Namespace) -> int:
    root: Path = args.root or Path.cwd()
    try:
        record = load_case(args.case_id, root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    paths = case_root_paths(root, args.case_id)
    store = ArtifactStore(paths["artifacts_dir"])

    print(render(record.case, record.entries, store.read_only()), end="")
    return 0
