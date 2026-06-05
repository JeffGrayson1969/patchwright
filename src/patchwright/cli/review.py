"""`patchwright review` — open $EDITOR with evidence + decision, record outcome.

Flow:
  1. Load the case and render the evidence packet.
  2. Append a DECISION template the reviewer fills in.
  3. Write to a temp file, invoke $EDITOR, wait for save.
  4. Parse the decision verb + reason from the saved file.
  5. Append a human_decision entry to the journal with the reviewer's identity.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.cases import load_case
from patchwright.core.evidence import render
from patchwright.core.orchestrator import case_root_paths
from patchwright.core.reviews import (
    VALID_DECISIONS,
    Decision,
    record_human_decision,
    reviewer_identity,
)

DECISION_MARKER = "## Decision"
REASON_MARKER = "## Reason"

DECISION_TEMPLATE = f"""

---

{DECISION_MARKER}

<!-- Replace with exactly one of: approve | edit | reject | fork
     Leave the placeholder line in place to abort without recording. -->
TYPE_DECISION_HERE

{REASON_MARKER}

<!-- One-line rationale. Required for reject. Optional otherwise but recommended. -->
"""

ABORT_PLACEHOLDER = "TYPE_DECISION_HERE"


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = subparsers.add_parser(
        "review",
        help="Open a case in $EDITOR and record the reviewer's decision.",
        description=(
            "Renders the case as a markdown evidence packet, appends a decision "
            "template, opens $EDITOR. On save, parses the decision verb "
            "(approve/edit/reject/fork) and writes a human_decision journal entry."
        ),
    )
    p.add_argument("case_id", type=str)
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Persistence root. Defaults to cwd.",
    )
    p.add_argument(
        "--as",
        dest="identity_override",
        type=str,
        default=None,
        help=(
            "Override the reviewer identity. Default: git config user.email, "
            "then os.getlogin(), then 'unknown'."
        ),
    )
    p.set_defaults(func=cmd_review)


def cmd_review(args: argparse.Namespace) -> int:
    root: Path = args.root or Path.cwd()
    case_id: str = args.case_id

    try:
        record = load_case(case_id, root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    paths = case_root_paths(root, case_id)
    store = ArtifactStore(paths["artifacts_dir"])
    initial_content = render(record.case, record.entries, store.read_only()) + DECISION_TEMPLATE

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=f".{case_id}.md",
        delete=False,
        encoding="utf-8",
    ) as tf:
        tf.write(initial_content)
        temp_path = Path(tf.name)

    try:
        _invoke_editor(temp_path)
        saved = temp_path.read_text(encoding="utf-8")
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()

    decision, reason = _parse_decision(saved)
    if decision is None:
        print("aborted — no decision recorded", file=sys.stderr)
        return 2

    identity = reviewer_identity(override=args.identity_override)
    entry = record_human_decision(
        case_id=case_id,
        root=root,
        decision=decision,
        reason=reason,
        identity=identity,
    )
    print(
        f"recorded {decision!r} for {case_id} by {identity} "
        f"(seq={entry.seq}, hash={entry.content_hash[:19]})",
        file=sys.stderr,
    )
    return 0


def _invoke_editor(path: Path) -> None:
    """Open `path` in the user's editor and wait for it to exit.

    Honors $VISUAL then $EDITOR; falls back to `nano` then `vi`.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    # Split on whitespace so e.g. EDITOR="code --wait" works.
    cmd = [*editor.split(), str(path)]
    subprocess.run(cmd, check=True)


def _parse_decision(content: str) -> tuple[Decision | None, str]:
    """Extract the decision verb + reason from the saved markdown.

    Looks for the first non-comment non-blank line under ## Decision and the
    first non-comment non-blank line under ## Reason. Returns (None, "") if
    the placeholder TYPE_DECISION_HERE was left in place or no valid verb
    is found.
    """
    decision_text = _section_text(content, DECISION_MARKER)
    reason_text = _section_text(content, REASON_MARKER)

    verb = _first_meaningful_line(decision_text).lower()
    if verb == ABORT_PLACEHOLDER.lower() or not verb:
        return None, ""
    if verb not in VALID_DECISIONS:
        return None, ""

    reason = _first_meaningful_line(reason_text)
    return verb, reason


def _section_text(content: str, marker: str) -> str:
    """Return text between `marker` and the next ## heading (or EOF)."""
    idx = content.find(marker)
    if idx < 0:
        return ""
    rest = content[idx + len(marker) :]
    # Find next ## heading (any line starting with "## ")
    next_heading_idx = -1
    for line_start in _line_starts(rest):
        if rest.startswith("## ", line_start) or rest.startswith("##\n", line_start):
            next_heading_idx = line_start
            break
    if next_heading_idx > 0:
        return rest[:next_heading_idx]
    return rest


def _line_starts(text: str) -> list[int]:
    """Indices where each line begins (after the first)."""
    out: list[int] = []
    for i, ch in enumerate(text):
        if ch == "\n" and i + 1 < len(text):
            out.append(i + 1)
    return out


def _first_meaningful_line(section: str) -> str:
    """First non-blank, non-comment line in a section."""
    for raw in section.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<!--") or line.startswith("-->"):
            continue
        if line.startswith("---"):
            continue
        return line
    return ""
