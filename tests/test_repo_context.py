"""Unit tests for tools/repo_context.py — extract_symbol_snippet."""

from __future__ import annotations

from pathlib import Path

import pytest

from patchwright.tools.repo_context import SymbolNotFound, extract_symbol_snippet

# --------------------------------------------------------------------------- fixtures


SIMPLE_MODULE = """\
from __future__ import annotations

import os


def helper() -> str:
    return "hi"


def read_file(filename: str) -> str:
    with open(filename) as f:
        return f.read()
"""

CLASS_MODULE = """\
from __future__ import annotations

import json


class MyParser:
    def parse(self, data: str) -> dict:
        return json.loads(data)

    def _validate(self, obj: dict) -> bool:
        return bool(obj)
"""

DECORATED_MODULE = """\
from __future__ import annotations


def my_decorator(fn):
    return fn


@my_decorator
def decorated_func(x: int) -> int:
    return x * 2
"""

NESTED_MODULE = """\
from __future__ import annotations


def outer() -> None:
    def inner() -> None:
        pass
    inner()
"""

LARGE_MODULE_LINES = ["def big_func() -> None:"] + [f"    x{i} = {i}" for i in range(300)]
LARGE_MODULE = "\n".join(LARGE_MODULE_LINES) + "\n"


# --------------------------------------------------------------------------- helpers


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- tests: module-level function


def test_module_level_function_found(tmp_path: Path) -> None:
    p = _write(tmp_path, "m.py", SIMPLE_MODULE)
    snippet = extract_symbol_snippet(p, "read_file")
    assert "def read_file" in snippet
    assert "open(filename)" in snippet


def test_module_imports_included(tmp_path: Path) -> None:
    p = _write(tmp_path, "m.py", SIMPLE_MODULE)
    snippet = extract_symbol_snippet(p, "read_file")
    # Imports should appear above the definition.
    assert "import os" in snippet
    assert snippet.index("import os") < snippet.index("def read_file")


def test_symbol_absent_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "m.py", SIMPLE_MODULE)
    with pytest.raises(SymbolNotFound, match="no_such_fn"):
        extract_symbol_snippet(p, "no_such_fn")


# --------------------------------------------------------------------------- tests: class methods


def test_class_method_dotted(tmp_path: Path) -> None:
    p = _write(tmp_path, "c.py", CLASS_MODULE)
    snippet = extract_symbol_snippet(p, "MyParser.parse")
    assert "def parse" in snippet
    assert "json.loads" in snippet


def test_class_method_private(tmp_path: Path) -> None:
    p = _write(tmp_path, "c.py", CLASS_MODULE)
    snippet = extract_symbol_snippet(p, "MyParser._validate")
    assert "def _validate" in snippet


def test_class_not_found_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "c.py", CLASS_MODULE)
    with pytest.raises(SymbolNotFound, match="NoSuchClass"):
        extract_symbol_snippet(p, "NoSuchClass.parse")


def test_method_not_in_class_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "c.py", CLASS_MODULE)
    with pytest.raises(SymbolNotFound, match="no_method"):
        extract_symbol_snippet(p, "MyParser.no_method")


# --------------------------------------------------------------------------- tests: decorated function


def test_decorated_function_found(tmp_path: Path) -> None:
    p = _write(tmp_path, "d.py", DECORATED_MODULE)
    snippet = extract_symbol_snippet(p, "decorated_func")
    assert "def decorated_func" in snippet


# --------------------------------------------------------------------------- tests: nested function


def test_nested_function_outer_found(tmp_path: Path) -> None:
    # We only search module-level and one class-deep; nested inner() is not
    # directly addressable — the outer function is returned.
    p = _write(tmp_path, "n.py", NESTED_MODULE)
    snippet = extract_symbol_snippet(p, "outer")
    assert "def outer" in snippet


# --------------------------------------------------------------------------- tests: line cap


def test_max_lines_truncation(tmp_path: Path) -> None:
    p = _write(tmp_path, "big.py", LARGE_MODULE)
    snippet = extract_symbol_snippet(p, "big_func", max_lines=10)
    lines = snippet.splitlines()
    # Last line must be the truncation comment.
    assert "truncated" in lines[-1]
    # Total line count must not exceed max_lines + 1 (the truncation comment).
    assert len(lines) <= 11


def test_short_function_not_truncated(tmp_path: Path) -> None:
    p = _write(tmp_path, "m.py", SIMPLE_MODULE)
    snippet = extract_symbol_snippet(p, "helper", max_lines=200)
    assert "truncated" not in snippet


# --------------------------------------------------------------------------- tests: fixture files


def test_cwe22_vulnerable_snippet(tmp_path: Path) -> None:
    """Smoke-test against the real CWE-22 fixture used in integration tests."""
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "patch_corpus"
        / "cwe22_path_traversal"
        / "vulnerable.py"
    )
    snippet = extract_symbol_snippet(fixture, "read_file")
    assert "open(filename)" in snippet
