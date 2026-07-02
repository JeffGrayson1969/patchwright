"""JournalCipher + key handling (AEG-376, T4)."""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.exceptions import InvalidTag

from patchwright.core.config import EmbargoConfig, PatchwrightConfig
from patchwright.core.journal_crypto import (
    JOURNAL_KEY_NAME,
    JournalCipher,
    JournalKeyError,
    cipher_for_reading,
    cipher_for_writing,
    generate_key_b64,
    is_encrypted_envelope,
)
from patchwright.core.secrets import SecretNotFound


def _no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no key resolves — mirrors get_secret: raise when required, else None."""
    monkeypatch.delenv(JOURNAL_KEY_NAME, raising=False)

    def fake(key: str, *, required: bool = True) -> str | None:
        if required:
            raise SecretNotFound(key)
        return None

    monkeypatch.setattr("patchwright.core.journal_crypto.get_secret", fake)


def _set_key(monkeypatch: pytest.MonkeyPatch, b64: str) -> None:
    monkeypatch.setattr("patchwright.core.journal_crypto.get_secret", lambda *a, **k: b64)


# --------------------------------------------------------------------------- cipher


def test_encrypt_decrypt_round_trip() -> None:
    cipher = JournalCipher(key=base64.b64decode(generate_key_b64()))
    pt = b'{"author":"x","payload":{"k":"v"}}'
    envelope = cipher.encrypt_line(pt)
    assert cipher.decrypt_line(json.loads(envelope)) == pt


def test_encrypt_line_is_envelope_not_plaintext() -> None:
    cipher = JournalCipher(key=base64.b64decode(generate_key_b64()))
    envelope = cipher.encrypt_line(b"SECRET_MARKER_12345")
    assert b"SECRET_MARKER_12345" not in envelope
    assert b"pw_enc" in envelope


def test_nonce_makes_ciphertext_nondeterministic() -> None:
    cipher = JournalCipher(key=base64.b64decode(generate_key_b64()))
    assert cipher.encrypt_line(b"same") != cipher.encrypt_line(b"same")


def test_wrong_key_cannot_decrypt() -> None:
    envelope = JournalCipher(key=base64.b64decode(generate_key_b64())).encrypt_line(b"data")
    other = JournalCipher(key=base64.b64decode(generate_key_b64()))
    with pytest.raises(InvalidTag):
        other.decrypt_line(json.loads(envelope))


def test_cipher_rejects_wrong_key_length() -> None:
    with pytest.raises(JournalKeyError):
        JournalCipher(key=b"tooshort")


def test_generate_key_is_32_bytes() -> None:
    assert len(base64.b64decode(generate_key_b64())) == 32


def test_is_encrypted_envelope() -> None:
    assert is_encrypted_envelope({"pw_enc": "AESGCM-256-v1", "ct": "..."})
    assert not is_encrypted_envelope({"author": "x", "content_hash": "y"})
    assert not is_encrypted_envelope("not a dict")


# --------------------------------------------------------------------------- key loading


def test_load_key_from_env_via_get_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    b64 = generate_key_b64()
    _set_key(monkeypatch, b64)
    cipher = cipher_for_reading()
    assert cipher is not None and cipher.key == base64.b64decode(b64)


def test_malformed_base64_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "not!!base64!!")
    with pytest.raises(JournalKeyError):
        cipher_for_reading()


def test_wrong_length_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, base64.b64encode(b"only16byteslong!!").decode())
    with pytest.raises(JournalKeyError):
        cipher_for_reading()


# --------------------------------------------------------------------------- policy


def test_writing_cipher_none_in_normal_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, generate_key_b64())  # key present, but mode=normal
    config = PatchwrightConfig(embargo=EmbargoConfig(mode="normal"))
    assert cipher_for_writing(config) is None


def test_writing_cipher_present_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, generate_key_b64())
    config = PatchwrightConfig(embargo=EmbargoConfig(mode="strict"))
    assert cipher_for_writing(config) is not None


def test_writing_strict_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_key(monkeypatch)
    config = PatchwrightConfig(embargo=EmbargoConfig(mode="strict"))
    with pytest.raises(SecretNotFound):
        cipher_for_writing(config)  # required=True by default — refuse plaintext embargo


def test_reading_cipher_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_key(monkeypatch)
    assert cipher_for_reading() is None
