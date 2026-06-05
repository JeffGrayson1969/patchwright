from __future__ import annotations

from patchwright_helpers import safe_path


def read_file(filename):
    with open(safe_path(filename)) as f:
        return f.read()
