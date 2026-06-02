from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH: str = "sha256:" + "0" * 64


def canonical_json(obj: Any) -> bytes:
    """Stable, deterministic JSON encoding for hashing.

    UTF-8, sorted keys, no extra whitespace, no NaN/Inf. Compatible across
    Python minor versions and json libraries that follow RFC 8259.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_b16(data: bytes) -> str:
    """Return 'sha256:<hex>' for the given bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_for(obj: Any) -> str:
    """Convenience: canonical-JSON then sha256_b16."""
    return sha256_b16(canonical_json(obj))
