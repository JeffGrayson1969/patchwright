"""Test #1 — content addressing of journal entries.

Invariant: identical canonical envelope -> identical content_hash; any byte
change in the envelope -> different content_hash; the (non-hashed) signature
field MUST NOT affect content_hash.
"""

from __future__ import annotations

from patchwright.core.hashing import GENESIS_HASH
from patchwright.core.journal import compute_content_hash
from patchwright.core.models import JournalEntry


def _envelope(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "seq": 0,
        "case_id": "case-x",
        "ts": "2026-06-02T12:00:00.000000Z",
        "kind": "case_opened",
        "author": "system:orchestrator",
        "prev_hash": GENESIS_HASH,
        "payload": {"a": 1, "b": "two"},
    }
    base.update(overrides)
    return base


def test_identical_envelopes_have_identical_hash() -> None:
    h1 = compute_content_hash(**_envelope())  # type: ignore[arg-type]
    h2 = compute_content_hash(**_envelope())  # type: ignore[arg-type]
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == len("sha256:") + 64


def test_payload_change_changes_hash() -> None:
    h1 = compute_content_hash(**_envelope())  # type: ignore[arg-type]
    h2 = compute_content_hash(**_envelope(payload={"a": 1, "b": "TWO"}))  # type: ignore[arg-type]
    assert h1 != h2


def test_seq_or_ts_change_changes_hash() -> None:
    h1 = compute_content_hash(**_envelope())  # type: ignore[arg-type]
    h2 = compute_content_hash(**_envelope(seq=1))  # type: ignore[arg-type]
    h3 = compute_content_hash(**_envelope(ts="2026-06-02T12:00:00.000001Z"))  # type: ignore[arg-type]
    assert {h1, h2, h3} == {h1, h2, h3}
    assert h1 != h2
    assert h1 != h3
    assert h2 != h3


def test_signature_does_not_affect_hash() -> None:
    """signature is OUTSIDE the hash envelope (FR-PV-4 forward-compat)."""
    h_no_sig = compute_content_hash(**_envelope())  # type: ignore[arg-type]
    e = JournalEntry(
        **_envelope(),  # type: ignore[arg-type]
        content_hash=h_no_sig,
        signature=None,
    )
    e_signed = e.model_copy(update={"signature": "sig:fake"})
    # Recompute against the *signed* entry's envelope — same envelope so same hash.
    expected = compute_content_hash(
        seq=e_signed.seq,
        case_id=e_signed.case_id,
        ts=e_signed.ts,
        kind=e_signed.kind,
        author=e_signed.author,
        prev_hash=e_signed.prev_hash,
        payload=e_signed.payload,
    )
    assert expected == h_no_sig == e_signed.content_hash
