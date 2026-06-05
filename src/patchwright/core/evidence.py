"""Render a Case + journal entries as a markdown evidence packet (FR-HR-1).

The packet is what a human reviewer sees in $EDITOR when they run
`patchwright review <case-id>`. It must be self-contained — the reviewer
should not need to open a debugger or read the journal raw to decide.

Sections (in render order):
  - Header (case id, state, created_at, last_hash)
  - Origin (raw_report summary if available)
  - Timeline (one row per journal entry)
  - Artifacts (each artifact, with inline content for known kinds)
  - Reasoning trace (collected from transition `reason` fields)
"""

from __future__ import annotations

import json
from typing import Any

from patchwright.core.artifacts import ReadOnlyArtifactStore
from patchwright.core.models import Artifact, Case, JournalEntry

INLINE_ARTIFACT_KINDS = frozenset({"triage_packet", "raw_report"})
"""Artifact kinds we render inline in the evidence packet. Others are
referenced by sha but not embedded — operators can fetch them separately."""


def render(
    case: Case,
    entries: list[JournalEntry],
    store: ReadOnlyArtifactStore,
) -> str:
    """Return the markdown evidence packet for one case."""
    parts: list[str] = []
    parts.append(_header(case))
    parts.append(_origin(case, store))
    parts.append(_timeline(entries))
    parts.append(_artifacts(case, store))
    parts.append(_reasoning(entries))
    return "\n\n".join(p for p in parts if p) + "\n"


# --------------------------------------------------------------------------- sections


def _header(case: Case) -> str:
    return (
        f"# Case `{case.id}`\n\n"
        f"- **State:** `{case.state}`\n"
        f"- **Created:** {case.created_at}\n"
        f"- **Last entry seq:** {case.last_seq}\n"
        f"- **Last hash:** `{_short(case.last_hash)}`\n"
        f"- **Artifacts attached:** {len(case.artifacts)}"
    )


def _origin(case: Case, store: ReadOnlyArtifactStore) -> str:
    raw = _first_artifact(case, "raw_report")
    if raw is None:
        return "## Origin\n\n_(no raw_report artifact on this case)_"

    body = _safe_read(store, raw.id)
    excerpt = _excerpt(body, max_chars=2000)
    return (
        "## Origin\n\n"
        f"- **kind:** `{raw.kind}`\n"
        f"- **media_type:** `{raw.media_type}`\n"
        f"- **size:** {raw.size} bytes\n"
        f"- **sha:** `{_short(raw.id)}`\n\n"
        "```\n"
        f"{excerpt}\n"
        "```"
    )


def _timeline(entries: list[JournalEntry]) -> str:
    if not entries:
        return "## Timeline\n\n_(no entries)_"
    rows = ["| seq | kind | author | hash |", "|----:|------|--------|------|"]
    for e in entries:
        rows.append(f"| {e.seq} | `{e.kind}` | `{e.author}` | `{_short(e.content_hash)}` |")
    return "## Timeline\n\n" + "\n".join(rows)


def _artifacts(case: Case, store: ReadOnlyArtifactStore) -> str:
    if not case.artifacts:
        return "## Artifacts\n\n_(none attached)_"

    chunks: list[str] = ["## Artifacts"]
    for a in case.artifacts:
        chunks.append(_one_artifact(a, store))
    return "\n\n".join(chunks)


def _one_artifact(a: Artifact, store: ReadOnlyArtifactStore) -> str:
    meta = (
        f"### `{a.kind}` ({_short(a.id)})\n\n"
        f"- **media_type:** `{a.media_type}`\n"
        f"- **size:** {a.size} bytes"
    )
    if a.kind not in INLINE_ARTIFACT_KINDS:
        return meta

    body = _safe_read(store, a.id)
    pretty = _pretty_json_or_text(body, max_chars=3000)
    return meta + "\n\n```\n" + pretty + "\n```"


def _reasoning(entries: list[JournalEntry]) -> str:
    reasons: list[str] = []
    for e in entries:
        reason = e.payload.get("reason")
        if reason:
            reasons.append(f"- **seq {e.seq}** ({e.author}): {reason}")
    if not reasons:
        return "## Reasoning trace\n\n_(no reasoning recorded)_"
    return "## Reasoning trace\n\n" + "\n".join(reasons)


# --------------------------------------------------------------------------- helpers


def _short(content_hash: str) -> str:
    """Trim 'sha256:abc…xyz' to 12 hex chars for display."""
    if content_hash.startswith("sha256:"):
        return content_hash[7:19]
    return content_hash[:12]


def _first_artifact(case: Case, kind: str) -> Artifact | None:
    for a in case.artifacts:
        if a.kind == kind:
            return a
    return None


def _safe_read(store: ReadOnlyArtifactStore, sha: str) -> bytes:
    try:
        return store.get(sha)
    except Exception as exc:
        return f"<artifact unreadable: {type(exc).__name__}: {exc}>".encode()


def _excerpt(body: bytes, *, max_chars: int) -> str:
    text = body.decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n…[truncated, {len(text) - max_chars} chars]"


def _pretty_json_or_text(body: bytes, *, max_chars: int) -> str:
    try:
        parsed: Any = json.loads(body)
        pretty = json.dumps(parsed, indent=2, sort_keys=True)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _excerpt(body, max_chars=max_chars)
    return _excerpt(pretty.encode("utf-8"), max_chars=max_chars)


__all__ = ["INLINE_ARTIFACT_KINDS", "render"]
