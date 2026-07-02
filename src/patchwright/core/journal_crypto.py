"""Embargoed-case journal encryption (AEG-376, T4).

Encrypts journal entries at rest so an exfiltrated journal for an embargoed
case is useless without the operator key. Symmetric **AES-256-GCM**:

  - Quantum-resistant. AES-256 loses only half its bits to Grover's algorithm
    (128-bit effective) — still infeasible. This is data at rest with a key the
    operator holds locally; there is no key exchange, so no quantum-vulnerable
    asymmetric step and no need for a PQC KEM.
  - age/sops (the PRD's original suggestion) is deliberately NOT used: age
    recipient mode is X25519, which Shor's algorithm breaks — it would fail the
    quantum-resistance requirement. If asymmetric multi-recipient is ever
    needed, the PQ path is a hybrid X25519+ML-KEM KEM (future work).

Encryption is per-line: each JSONL line is either plaintext (a JournalEntry) or
an encryption envelope `{"ct": "<b64(nonce||ciphertext||tag)>", "pw_enc": "..."}`.
The Merkle chain / content_hash are computed over the plaintext envelope before
encryption, so replay + integrity checks are unchanged once decrypted.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from patchwright.core.config import PatchwrightConfig
from patchwright.core.hashing import canonical_json
from patchwright.core.secrets import get_secret

JOURNAL_KEY_NAME = "PATCHWRIGHT_JOURNAL_KEY"
"""secrets.py lookup name (OS keychain / env) for the base64-encoded 32-byte key."""

_SCHEME = "AESGCM-256-v1"
_NONCE_BYTES = 12
_KEY_BYTES = 32


class JournalKeyError(ValueError):
    """The journal key is missing or malformed."""


@dataclass(frozen=True)
class JournalCipher:
    """AES-256-GCM line cipher. Holds a 32-byte key; never logs it."""

    key: bytes

    def __post_init__(self) -> None:
        if len(self.key) != _KEY_BYTES:
            raise JournalKeyError(f"journal key must be {_KEY_BYTES} bytes, got {len(self.key)}")

    def encrypt_line(self, plaintext: bytes) -> bytes:
        """Return a canonical-JSON encryption envelope for one plaintext line."""
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(self.key).encrypt(nonce, plaintext, None)
        blob = base64.b64encode(nonce + ct).decode("ascii")
        return canonical_json({"ct": blob, "pw_enc": _SCHEME})

    def decrypt_line(self, envelope: dict[str, object]) -> bytes:
        """Recover the plaintext line from a parsed encryption envelope."""
        raw = envelope.get("ct")
        if not isinstance(raw, str):
            raise JournalKeyError("encryption envelope missing 'ct'")
        blob = base64.b64decode(raw)
        nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return AESGCM(self.key).decrypt(nonce, ct, None)


def is_encrypted_envelope(obj: object) -> bool:
    """True iff a parsed JSONL object is an encryption envelope (not an entry)."""
    return isinstance(obj, dict) and "pw_enc" in obj


def generate_key_b64() -> str:
    """A fresh base64-encoded 32-byte key for an operator to store in the keychain."""
    return base64.b64encode(os.urandom(_KEY_BYTES)).decode("ascii")


def _load_key(*, required: bool) -> bytes | None:
    b64 = get_secret(JOURNAL_KEY_NAME, required=required)
    if b64 is None:
        return None
    try:
        key = base64.b64decode(b64, validate=True)
    except Exception as exc:  # malformed base64
        raise JournalKeyError(f"{JOURNAL_KEY_NAME} is not valid base64") from exc
    if len(key) != _KEY_BYTES:
        raise JournalKeyError(f"{JOURNAL_KEY_NAME} must decode to {_KEY_BYTES} bytes")
    return key


def cipher_for_writing(config: PatchwrightConfig, *, required: bool = True) -> JournalCipher | None:
    """Cipher for the write path: encrypt iff embargo.mode == 'strict'.

    In strict mode the key is mandatory (required=True) — refuse to write an
    embargoed journal in plaintext.
    """
    if config.embargo.mode != "strict":
        return None
    key = _load_key(required=required)
    return JournalCipher(key) if key is not None else None


def cipher_for_reading() -> JournalCipher | None:
    """Cipher for the read path: use the operator key whenever it's available,
    independent of embargo.mode (a journal may have been encrypted under a strict
    run that has since ended). Returns None when no key is configured."""
    key = _load_key(required=False)
    return JournalCipher(key) if key is not None else None


__all__ = [
    "JOURNAL_KEY_NAME",
    "JournalCipher",
    "JournalKeyError",
    "cipher_for_reading",
    "cipher_for_writing",
    "generate_key_b64",
    "is_encrypted_envelope",
]
