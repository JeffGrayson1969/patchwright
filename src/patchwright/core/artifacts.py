from __future__ import annotations

import os
from pathlib import Path

from patchwright.core.errors import ArtifactMissing
from patchwright.core.hashing import sha256_b16


def _fsync_dir(path: Path) -> None:
    """fsync a directory so a rename or new file is durable."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ArtifactStore:
    """Content-addressed blob store: <root>/<sha256_hex>.bin."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, sha: str) -> Path:
        if not sha.startswith("sha256:"):
            raise ValueError(f"expected 'sha256:<hex>', got {sha!r}")
        return self.root / f"{sha.split(':', 1)[1]}.bin"

    def put(self, data: bytes) -> str:
        """Write data; return its 'sha256:<hex>' id. Idempotent."""
        sha = sha256_b16(data)
        final = self._path_for(sha)
        if final.exists():
            return sha
        tmp = final.with_suffix(".bin.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
        _fsync_dir(self.root)
        return sha

    def get(self, sha: str) -> bytes:
        path = self._path_for(sha)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactMissing(sha) from exc

    def has(self, sha: str) -> bool:
        return self._path_for(sha).exists()

    def read_only(self) -> ReadOnlyArtifactStore:
        return ReadOnlyArtifactStore(self)


class ReadOnlyArtifactStore:
    """Read-only view handed to agents. Enforces 'agents never write disk'."""

    def __init__(self, backing: ArtifactStore) -> None:
        self._backing = backing

    def get(self, sha: str) -> bytes:
        return self._backing.get(sha)

    def has(self, sha: str) -> bool:
        return self._backing.has(sha)
