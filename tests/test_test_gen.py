"""test_gen_python.py — render a Python test file from a TestSpec.

These tests don't run the generated test against real code (that's M2-plan's
job in Wave B). Here we verify the generator produces:
  - syntactically valid Python (must parse)
  - correct imports
  - correct assertion shape for each `expects` value
  - rejects mis-shaped TestSpec at render time
"""

from __future__ import annotations

import ast

import pytest

from patchwright.models.patch_plan import TestSpec
from patchwright.tools.test_gen_python import render


def _base(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "file": "tests/test_x.py",
        "test_function_name": "test_x",
        "target_import": "myapp.users",
        "target_callable": "get_user",
        "call_expr": "target('alice')",
        "expects": "returns_none",
    }
    base.update(overrides)
    return base


def test_returns_none_renders_valid_python() -> None:
    spec = TestSpec.model_validate(_base())
    source = render(spec)
    ast.parse(source)
    assert "from __future__ import annotations" in source
    assert "from myapp.users import get_user as target" in source
    assert "result = target('alice')" in source
    assert "assert result is None" in source


def test_returns_empty_uses_truthiness_assert() -> None:
    spec = TestSpec.model_validate(_base(expects="returns_empty"))
    source = render(spec)
    ast.parse(source)
    assert "assert not result" in source


def test_raises_emits_pytest_raises() -> None:
    spec = TestSpec.model_validate(_base(expects="raises", expected_exception="ValueError"))
    source = render(spec)
    ast.parse(source)
    assert "import pytest" in source
    assert "with pytest.raises(ValueError):" in source


def test_raises_without_exception_class_rejected() -> None:
    spec = TestSpec.model_validate(_base(expects="raises", expected_exception=None))
    with pytest.raises(ValueError, match="expected_exception"):
        render(spec)


def test_setup_code_executes_before_call() -> None:
    spec = TestSpec.model_validate(
        _base(
            setup_code="import sqlite3\nconn = sqlite3.connect(':memory:')",
            call_expr="target(conn)",
        )
    )
    source = render(spec)
    ast.parse(source)
    setup_idx = source.index("conn = sqlite3.connect")
    call_idx = source.index("result = target(conn)")
    assert setup_idx < call_idx


def test_dotted_target_import() -> None:
    spec = TestSpec.model_validate(_base(target_import="my.deep.package"))
    source = render(spec)
    assert "from my.deep.package import" in source


def test_call_expr_must_be_expression_not_statement() -> None:
    spec = TestSpec.model_validate(_base(expects="returns_none", call_expr="x = target('a')"))
    with pytest.raises(ValueError, match="call_expr"):
        render(spec)
