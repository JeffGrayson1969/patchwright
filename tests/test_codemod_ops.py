"""Per-operation tests for the LibCST codemod. One test per operation type,
plus precondition-failure tests for each."""

from __future__ import annotations

from pathlib import Path

import pytest

from patchwright.models.patch_plan import (
    AddTestCase,
    InsertImport,
    PatchPlan,
    ReplaceFunctionBody,
    WrapCallWithValidator,
)
from patchwright.tools.codemod_python import CodemodError, apply


def _plan(*ops: object) -> PatchPlan:
    return PatchPlan(
        case_id="c",
        summary="t",
        operations=list(ops),  # type: ignore[arg-type]
        rationale="t",
    )


def _write(repo: Path, name: str, source: str) -> Path:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- InsertImport


def test_insert_import_simple(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "from __future__ import annotations\n\nx = 1\n")
    plan = _plan(InsertImport(file="a.py", module="json"))
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert "import json" in src
    assert src.index("import json") > src.index("from __future__")


def test_insert_import_from(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "from __future__ import annotations\n\nx = 1\n")
    plan = _plan(InsertImport(file="a.py", module="os.path", names=["join", "exists"]))
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert "from os.path import join, exists" in src


def test_insert_import_is_idempotent(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "from __future__ import annotations\n\nimport json\n")
    plan = _plan(InsertImport(file="a.py", module="json"))
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert src.count("import json") == 1


def test_insert_import_preserves_module_docstring(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        '"""module doc."""\nfrom __future__ import annotations\n\nx = 1\n',
    )
    plan = _plan(InsertImport(file="a.py", module="json"))
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert src.startswith('"""module doc."""')
    assert "import json" in src


# --------------------------------------------------------------------------- ReplaceFunctionBody


def test_replace_function_body_module_level(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "from __future__ import annotations\n\n\ndef foo(x):\n    return x + 1\n",
    )
    plan = _plan(
        ReplaceFunctionBody(file="a.py", function_qualname="foo", new_body="return x * 2\n")
    )
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert "return x * 2" in src
    assert "return x + 1" not in src


def test_replace_function_body_in_class(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        (
            "from __future__ import annotations\n\n\n"
            "class C:\n    def m(self, x):\n        return x + 1\n"
        ),
    )
    plan = _plan(
        ReplaceFunctionBody(file="a.py", function_qualname="C.m", new_body="return x * 2\n")
    )
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert "return x * 2" in src


def test_replace_function_body_missing_raises(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "from __future__ import annotations\n\n\ndef foo(): ...\n")
    plan = _plan(
        ReplaceFunctionBody(file="a.py", function_qualname="not_here", new_body="return None\n")
    )
    with pytest.raises(CodemodError, match="function not found"):
        apply(plan, tmp_path)


# --------------------------------------------------------------------------- WrapCallWithValidator


def test_wrap_call_with_validator(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "from __future__ import annotations\n\n\ndef f(p):\n    return open(p)\n",
    )
    plan = _plan(WrapCallWithValidator(file="a.py", call_name="open", wrapper="safe_path"))
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert "open(safe_path(p))" in src


def test_wrap_call_is_idempotent(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "from __future__ import annotations\n\n\ndef f(p):\n    return open(safe_path(p))\n",
    )
    plan = _plan(WrapCallWithValidator(file="a.py", call_name="open", wrapper="safe_path"))
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "a.py").resolve()]
    assert src.count("safe_path") == 1


def test_wrap_call_no_match_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.py",
        "from __future__ import annotations\n\n\ndef f(p):\n    return p\n",
    )
    plan = _plan(WrapCallWithValidator(file="a.py", call_name="open", wrapper="safe_path"))
    with pytest.raises(CodemodError, match="no calls to"):
        apply(plan, tmp_path)


# --------------------------------------------------------------------------- AddTestCase


def test_add_test_case_new_file(tmp_path: Path) -> None:
    plan = _plan(
        AddTestCase(
            file="tests/test_new.py",
            test_function_name="test_added",
            test_body="assert True\n",
        )
    )
    out = apply(plan, tmp_path)
    src = out[(tmp_path / "tests" / "test_new.py").resolve()]
    assert "def test_added()" in src
    assert "assert True" in src
    assert "from __future__ import annotations" in src


def test_add_test_case_duplicate_name_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/test_existing.py",
        ("from __future__ import annotations\n\n\ndef test_x():\n    assert True\n"),
    )
    plan = _plan(
        AddTestCase(
            file="tests/test_existing.py",
            test_function_name="test_x",
            test_body="assert True\n",
        )
    )
    with pytest.raises(CodemodError, match="already exists"):
        apply(plan, tmp_path)


# --------------------------------------------------------------------------- path safety


def test_path_traversal_in_plan_rejected(tmp_path: Path) -> None:
    plan = _plan(InsertImport(file="../etc/passwd", module="json"))
    with pytest.raises(CodemodError, match="escapes repo root"):
        apply(plan, tmp_path)


def test_missing_target_file_raises(tmp_path: Path) -> None:
    plan = _plan(InsertImport(file="nonexistent.py", module="json"))
    with pytest.raises(CodemodError, match="target file does not exist"):
        apply(plan, tmp_path)
