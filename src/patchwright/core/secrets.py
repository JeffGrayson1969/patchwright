"""Secret resolution (NFR-S-10).

Lookup order for a credential:
  1. OS keychain via `keyring` (service='patchwright', username=key)
  2. Environment variable named `key`
  3. None

Never writes secrets to disk; never logs the value. Callers MUST validate
that returned secrets stay out of journal entries — see the test in
tests/test_secrets_not_in_journal.py.
"""

from __future__ import annotations

import logging
import os
from typing import Final

log = logging.getLogger(__name__)

SERVICE_NAME: Final[str] = "patchwright"


class SecretNotFound(LookupError):
    """The named secret was not found in keyring or environment."""


def get_secret(key: str, *, required: bool = True) -> str | None:
    """Resolve a secret by name. Tries OS keychain first, then environment.

    Args:
        key: e.g. 'ANTHROPIC_API_KEY'. The same name is used for both the
            keyring entry (under service 'patchwright') and the environment.
        required: if True, raise SecretNotFound when not present.

    Returns:
        The secret value, or None if `required=False` and not found.

    NEVER log the returned value. If you log key resolution at all, log only
    the *source* (keyring / env / missing).
    """
    keyring: object | None
    try:
        import keyring as _keyring  # noqa: PLC0415 - lazy: backends differ by OS
    except ImportError:  # pragma: no cover - keyring is a hard dep
        keyring = None
    else:
        keyring = _keyring

    if keyring is not None:
        try:
            value = keyring.get_password(SERVICE_NAME, key)
        except Exception as exc:
            log.debug("keyring lookup failed for %s: %s", key, type(exc).__name__)
            value = None
        if isinstance(value, str):
            log.debug("resolved %s from keyring", key)
            return value

    env_value = os.environ.get(key)
    if env_value:
        log.debug("resolved %s from env", key)
        return env_value

    if required:
        raise SecretNotFound(
            f"Secret {key!r} not found in OS keychain (service={SERVICE_NAME!r}) "
            f"or environment. Set it via `keyring set {SERVICE_NAME} {key}` "
            f"or `export {key}=...`."
        )
    return None
