from __future__ import annotations


def read_file(filename):
    with open(filename) as f:
        return f.read()
