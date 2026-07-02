"""`patchwright journal --case <id>` — print a case's journal entries.

For embargoed cases the on-disk journal is AES-256-GCM encrypted (T4, AEG-376);
this command decrypts with the operator key resolved from the OS keychain / env
(`PATCHWRIGHT_JOURNAL_KEY`). Without the key an encrypted journal cannot be
read — the command exits non-zero with a clear message rather than leaking
ciphertext or crashing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from patchwright.core.cases import load_case
from patchwright.core.errors import JournalEncrypted
from patchwright.core.journal_crypto import cipher_for_reading


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "journal",
        help="Print a case's journal entries (decrypting embargoed journals).",
        description=(
            "Reads the append-only journal for a case and prints each entry as "
            "JSON. Embargoed-case journals are encrypted at rest and require the "
            "operator key (PATCHWRIGHT_JOURNAL_KEY) to decrypt."
        ),
    )
    p.add_argument("--case", dest="case_id", type=str, required=True, help="Case id.")
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Persistence root containing journal/ and artifacts/ dirs. Defaults to cwd.",
    )
    p.set_defaults(func=cmd_journal)


def cmd_journal(args: argparse.Namespace) -> int:
    root: Path = args.root or Path.cwd()
    cipher = cipher_for_reading()

    try:
        record = load_case(args.case_id, root, cipher=cipher)
    except JournalEncrypted as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for entry in record.entries:
        print(json.dumps(entry.model_dump(mode="json"), sort_keys=True))
    return 0
