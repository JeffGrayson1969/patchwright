from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchwright.core.errors import ChainBroken, JournalCorrupt, JournalEncrypted
from patchwright.core.hashing import GENESIS_HASH, canonical_json, sha256_b16
from patchwright.core.journal_crypto import (
    JOURNAL_KEY_NAME,
    JournalCipher,
    is_encrypted_envelope,
)
from patchwright.core.models import EntryKind, JournalEntry

log = logging.getLogger(__name__)


def now_iso() -> str:
    """UTC, microsecond precision, 'Z' suffix — canonical for hashing."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _hashed_envelope(
    *,
    seq: int,
    case_id: str,
    ts: str,
    kind: str,
    author: str,
    prev_hash: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "author": author,
        "case_id": case_id,
        "kind": kind,
        "payload": payload,
        "prev_hash": prev_hash,
        "seq": seq,
        "ts": ts,
    }


def compute_content_hash(
    *,
    seq: int,
    case_id: str,
    ts: str,
    kind: str,
    author: str,
    prev_hash: str,
    payload: dict[str, Any],
) -> str:
    """Compute the content_hash for an entry. Signature is NOT in the hash."""
    return sha256_b16(
        canonical_json(
            _hashed_envelope(
                seq=seq,
                case_id=case_id,
                ts=ts,
                kind=kind,
                author=author,
                prev_hash=prev_hash,
                payload=payload,
            )
        )
    )


def verify_entry_hash(entry: JournalEntry) -> bool:
    expected = compute_content_hash(
        seq=entry.seq,
        case_id=entry.case_id,
        ts=entry.ts,
        kind=entry.kind,
        author=entry.author,
        prev_hash=entry.prev_hash,
        payload=entry.payload,
    )
    return expected == entry.content_hash


class Journal:
    """Per-case append-only JSONL with content-addressed Merkle chain.

    File layout: <dir>/journal.jsonl
    Each line is one JournalEntry serialized as canonical JSON + newline.
    """

    JOURNAL_FILENAME = "journal.jsonl"

    def __init__(self, dir_path: Path, *, cipher: JournalCipher | None = None) -> None:
        self.dir = dir_path
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / self.JOURNAL_FILENAME
        self.cipher = cipher
        """When set, entries are AES-256-GCM encrypted at rest (embargoed cases,
        T4). Reads transparently decrypt; a plaintext journal ignores it."""

    # ------------------------------------------------------------------ append

    def append(
        self,
        *,
        case_id: str,
        kind: EntryKind,
        author: str,
        payload: dict[str, Any],
        prev_hash: str,
        seq: int,
        ts: str | None = None,
    ) -> JournalEntry:
        """Append a new entry. Caller supplies seq and prev_hash for explicit
        consistency. Returns the persisted JournalEntry. Atomic per-line via fsync."""
        ts_val = ts if ts is not None else now_iso()
        content_hash = compute_content_hash(
            seq=seq,
            case_id=case_id,
            ts=ts_val,
            kind=kind,
            author=author,
            prev_hash=prev_hash,
            payload=payload,
        )
        full = {
            **_hashed_envelope(
                seq=seq,
                case_id=case_id,
                ts=ts_val,
                kind=kind,
                author=author,
                prev_hash=prev_hash,
                payload=payload,
            ),
            "content_hash": content_hash,
            "signature": None,
        }
        plaintext = canonical_json(full)
        payload_line = self.cipher.encrypt_line(plaintext) if self.cipher else plaintext
        line = payload_line + b"\n"
        with open(self.path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        _fsync_dir(self.dir)
        return JournalEntry.model_validate(full)

    # ------------------------------------------------------------------ read

    def read(self) -> list[JournalEntry]:
        """Read all valid entries; truncate a single torn-tail line if present.

        Raises ChainBroken if any non-tail entry fails hash or chain validation.
        """
        if not self.path.exists():
            return []

        raw = self.path.read_bytes()
        if not raw:
            return []
        # Allow exactly one torn (incomplete or invalid-JSON) trailing line.
        lines = raw.split(b"\n")
        # Trailing newline produces an empty final element; drop it.
        if lines and lines[-1] == b"":
            lines = lines[:-1]

        entries: list[JournalEntry] = []
        torn = False
        prev_hash = GENESIS_HASH

        for i, line in enumerate(lines):
            expected_seq = i  # implicit: per-case journal has no gaps
            try:
                obj = json.loads(line)
            except Exception as exc:
                # Torn tail: an incomplete (unparseable-JSON) line, only on the last line.
                if i == len(lines) - 1:
                    log.warning("torn tail line in %s; truncating", self.path)
                    torn = True
                    break
                raise JournalCorrupt(f"unparseable mid-journal at line {i}") from exc

            # A parseable line is committed: encrypted envelopes need the key;
            # any decrypt/validate failure here is corruption/tamper, not a torn tail.
            entry = self._decode_entry(obj, line, i)

            if entry.seq != expected_seq:
                raise JournalCorrupt(
                    f"seq gap at line {i}: expected {expected_seq}, got {entry.seq}"
                )
            if entry.prev_hash != prev_hash:
                raise ChainBroken(
                    f"chain break at line {i}: prev_hash {entry.prev_hash!r} "
                    f"!= previous content_hash {prev_hash!r}"
                )
            if not verify_entry_hash(entry):
                raise JournalCorrupt(f"content_hash mismatch at line {i}")

            entries.append(entry)
            prev_hash = entry.content_hash

        if torn:
            self._truncate_to(len(entries))

        return entries

    def _decode_entry(self, obj: object, line: bytes, i: int) -> JournalEntry:
        """Turn one parsed JSONL object into a JournalEntry, decrypting if needed."""
        if is_encrypted_envelope(obj):
            if self.cipher is None:
                raise JournalEncrypted(f"entry at line {i} is encrypted; supply {JOURNAL_KEY_NAME}")
            try:
                return JournalEntry.model_validate_json(self.cipher.decrypt_line(obj))  # type: ignore[arg-type]
            except JournalEncrypted:
                raise
            except Exception as exc:
                raise JournalCorrupt(f"cannot decrypt/parse entry at line {i}") from exc
        try:
            return JournalEntry.model_validate_json(line)
        except Exception as exc:
            raise JournalCorrupt(f"invalid entry at line {i}") from exc

    def _truncate_to(self, valid_entry_count: int) -> None:
        """Rewrite the file containing only the first `valid_entry_count` entries.

        Called when read() finds a torn tail. We re-serialize from parsed entries
        (which are byte-identical via canonical JSON), then atomic-rename.
        """
        entries = self.read_tolerant_raw_lines()[:valid_entry_count]
        tmp = self.path.with_suffix(".jsonl.tmp")
        with open(tmp, "wb") as f:
            for raw in entries:
                f.write(raw)
                f.write(b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        _fsync_dir(self.dir)

    def read_tolerant_raw_lines(self) -> list[bytes]:
        """Return raw bytes of each line that parses as a JournalEntry."""
        if not self.path.exists():
            return []
        out: list[bytes] = []
        for line in self.path.read_bytes().split(b"\n"):
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if is_encrypted_envelope(obj):
                out.append(line)  # keep encrypted lines verbatim; no key needed to truncate
                continue
            try:
                JournalEntry.model_validate_json(line)
            except Exception:
                continue
            out.append(line)
        return out

    # ------------------------------------------------------------------ iter

    def iter_entries(self) -> Iterator[JournalEntry]:
        yield from self.read()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
