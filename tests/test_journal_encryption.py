"""Journal encryption at rest for embargoed cases (AEG-376, T4)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from patchwright.core.errors import JournalCorrupt, JournalEncrypted
from patchwright.core.hashing import GENESIS_HASH
from patchwright.core.journal import Journal
from patchwright.core.journal_crypto import JournalCipher, generate_key_b64


def _cipher() -> JournalCipher:
    return JournalCipher(key=base64.b64decode(generate_key_b64()))


def _append(journal: Journal, *, seq: int, prev_hash: str, marker: str) -> str:
    entry = journal.append(
        case_id="case-embargo01",
        kind="case_opened",
        author="system:orchestrator",
        payload={"secret": marker},
        prev_hash=prev_hash,
        seq=seq,
    )
    return entry.content_hash


def test_encrypted_journal_round_trips(tmp_path: Path) -> None:
    cipher = _cipher()
    j = Journal(tmp_path, cipher=cipher)
    h0 = _append(j, seq=0, prev_hash=GENESIS_HASH, marker="SECRET_MARKER_XYZ")
    _append(j, seq=1, prev_hash=h0, marker="second")

    entries = Journal(tmp_path, cipher=cipher).read()
    assert [e.seq for e in entries] == [0, 1]
    assert entries[0].payload["secret"] == "SECRET_MARKER_XYZ"


def test_ciphertext_on_disk_has_no_plaintext(tmp_path: Path) -> None:
    j = Journal(tmp_path, cipher=_cipher())
    _append(j, seq=0, prev_hash=GENESIS_HASH, marker="SECRET_MARKER_XYZ")

    raw = (tmp_path / Journal.JOURNAL_FILENAME).read_bytes()
    assert b"SECRET_MARKER_XYZ" not in raw
    assert b"case_opened" not in raw  # even the kind is encrypted
    assert b"pw_enc" in raw


def test_read_without_key_raises(tmp_path: Path) -> None:
    j = Journal(tmp_path, cipher=_cipher())
    _append(j, seq=0, prev_hash=GENESIS_HASH, marker="x")

    with pytest.raises(JournalEncrypted):
        Journal(tmp_path).read()  # no cipher


def test_read_with_wrong_key_raises_corrupt(tmp_path: Path) -> None:
    j = Journal(tmp_path, cipher=_cipher())
    _append(j, seq=0, prev_hash=GENESIS_HASH, marker="x")

    with pytest.raises(JournalCorrupt):
        Journal(tmp_path, cipher=_cipher()).read()  # different key


def test_tampered_ciphertext_raises_corrupt(tmp_path: Path) -> None:
    cipher = _cipher()
    j = Journal(tmp_path, cipher=cipher)
    _append(j, seq=0, prev_hash=GENESIS_HASH, marker="x")

    path = tmp_path / Journal.JOURNAL_FILENAME
    obj = json.loads(path.read_bytes().splitlines()[0])
    blob = bytearray(base64.b64decode(obj["ct"]))
    blob[-1] ^= 0x01  # flip a bit in the tag
    obj["ct"] = base64.b64encode(bytes(blob)).decode()
    path.write_bytes(json.dumps(obj).encode() + b"\n")

    with pytest.raises(JournalCorrupt):
        Journal(tmp_path, cipher=cipher).read()


def test_chain_verifies_after_decrypt(tmp_path: Path) -> None:
    cipher = _cipher()
    j = Journal(tmp_path, cipher=cipher)
    h0 = _append(j, seq=0, prev_hash=GENESIS_HASH, marker="a")
    h1 = _append(j, seq=1, prev_hash=h0, marker="b")
    _append(j, seq=2, prev_hash=h1, marker="c")

    entries = Journal(tmp_path, cipher=cipher).read()
    prev = GENESIS_HASH
    for e in entries:
        assert e.prev_hash == prev
        prev = e.content_hash


def test_plaintext_journal_unaffected(tmp_path: Path) -> None:
    j = Journal(tmp_path)  # no cipher
    _append(j, seq=0, prev_hash=GENESIS_HASH, marker="visible")

    raw = (tmp_path / Journal.JOURNAL_FILENAME).read_bytes()
    assert b"visible" in raw
    assert b"pw_enc" not in raw
    # Readable with or without a cipher (per-line detection sees plaintext).
    assert Journal(tmp_path).read()[0].payload["secret"] == "visible"
    assert Journal(tmp_path, cipher=_cipher()).read()[0].payload["secret"] == "visible"


def test_torn_tail_tolerated_on_encrypted_journal(tmp_path: Path) -> None:
    cipher = _cipher()
    j = Journal(tmp_path, cipher=cipher)
    _append(j, seq=0, prev_hash=GENESIS_HASH, marker="a")

    # Simulate a torn (incomplete) trailing encrypted line.
    path = tmp_path / Journal.JOURNAL_FILENAME
    with open(path, "ab") as f:
        f.write(b'{"ct":"AAAA","pw_')  # unparseable JSON tail

    entries = Journal(tmp_path, cipher=cipher).read()
    assert len(entries) == 1  # torn tail dropped, first entry intact
