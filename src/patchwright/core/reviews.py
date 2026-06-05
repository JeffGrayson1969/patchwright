"""Human-review journal events (FR-HR-2/3).

The review CLI parses an operator's decision from the edited evidence packet
and calls record_human_decision() here. This module owns the on-disk side of
that — the orchestrator stays out of it (the FSM does not currently transition
on human_decision events; a human-confirmed action goes through a separate
agent invocation in M4+ work).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from patchwright.core.artifacts import ArtifactStore
from patchwright.core.journal import Journal
from patchwright.core.models import JournalEntry
from patchwright.core.orchestrator import case_root_paths, replay

Decision = Literal["approve", "edit", "reject", "fork"]
VALID_DECISIONS: frozenset[Decision] = frozenset(("approve", "edit", "reject", "fork"))

UNKNOWN_REVIEWER = "unknown"


def reviewer_identity(override: str | None = None) -> str:
    """Resolve the reviewer identity. Priority:

    1. --as CLI override
    2. `git config user.email`
    3. `os.getlogin()`
    4. 'unknown'
    """
    if override and override.strip():
        return override.strip()

    git_email = _git_user_email()
    if git_email:
        return git_email

    try:
        return os.getlogin()
    except OSError:
        return UNKNOWN_REVIEWER


def _git_user_email() -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    email = result.stdout.strip()
    return email or None


def record_human_decision(
    *,
    case_id: str,
    root: Path,
    decision: Decision,
    reason: str,
    identity: str,
) -> JournalEntry:
    """Append a human_decision entry to a case's journal.

    Does NOT transition the FSM — recording the decision is informational.
    A follow-up agent invocation can act on it (e.g. approving a PATCH_PROPOSED
    case triggers M2-pr to open the PR).
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(f"unknown decision {decision!r}; valid: {sorted(VALID_DECISIONS)}")

    paths = case_root_paths(root, case_id)
    journal = Journal(paths["journal_dir"])
    store = ArtifactStore(paths["artifacts_dir"])

    case = replay(journal, store)
    if case is None:
        raise FileNotFoundError(f"case {case_id!r} has no journal under {root}")

    return journal.append(
        case_id=case_id,
        kind="human_decision",
        author=f"human:{identity}",
        payload={
            "decision": decision,
            "reason": reason,
            "case_state_at_review": case.state,
        },
        prev_hash=case.last_hash,
        seq=case.last_seq + 1,
    )


__all__ = [
    "UNKNOWN_REVIEWER",
    "VALID_DECISIONS",
    "Decision",
    "record_human_decision",
    "reviewer_identity",
]
