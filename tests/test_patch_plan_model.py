"""PatchPlan Pydantic model — discriminated union, validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from patchwright.models.patch_plan import (
    AddTestCase,
    InsertImport,
    PatchPlan,
    ReplaceFunctionBody,
    TestSpec,
    WrapCallWithValidator,
)


def _minimal_plan() -> dict[str, object]:
    return {
        "case_id": "c",
        "summary": "fix",
        "operations": [
            {"type": "insert_import", "file": "a.py", "module": "json"},
        ],
        "rationale": "because",
    }


def test_minimal_plan_validates() -> None:
    p = PatchPlan.model_validate(_minimal_plan())
    assert len(p.operations) == 1
    assert isinstance(p.operations[0], InsertImport)
    assert p.schema_version == "1"


def test_each_operation_type_dispatches_correctly() -> None:
    ops = [
        {"type": "insert_import", "file": "a.py", "module": "json"},
        {
            "type": "replace_function_body",
            "file": "a.py",
            "function_qualname": "foo",
            "new_body": "return 1\n",
        },
        {
            "type": "wrap_call_with_validator",
            "file": "a.py",
            "call_name": "open",
            "wrapper": "safe",
        },
        {
            "type": "add_test_case",
            "file": "tests/test_a.py",
            "test_function_name": "test_x",
            "test_body": "assert True\n",
        },
    ]
    p = PatchPlan.model_validate({**_minimal_plan(), "operations": ops})
    types = [type(op).__name__ for op in p.operations]
    assert types == [
        "InsertImport",
        "ReplaceFunctionBody",
        "WrapCallWithValidator",
        "AddTestCase",
    ]


def test_unknown_op_type_rejected() -> None:
    with pytest.raises(ValidationError):
        PatchPlan.model_validate(
            {**_minimal_plan(), "operations": [{"type": "delete_world", "file": "a.py"}]}
        )


def test_empty_operations_list_rejected() -> None:
    with pytest.raises(ValidationError):
        PatchPlan.model_validate({**_minimal_plan(), "operations": []})


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        PatchPlan.model_validate({**_minimal_plan(), "secret_field": "x"})


def test_test_function_name_must_start_with_test() -> None:
    with pytest.raises(ValidationError):
        AddTestCase(file="t.py", test_function_name="not_test", test_body="pass\n")


def test_wrap_call_arg_index_default_zero() -> None:
    op = WrapCallWithValidator(file="a.py", call_name="open", wrapper="safe")
    assert op.arg_index == 0


def test_test_spec_raises_requires_expected_exception() -> None:
    """Validation lives in test_gen, but the model permits both shapes; this
    test pins the model behavior so we know where to add validation later
    if we want it earlier."""
    s = TestSpec(
        file="tests/test_x.py",
        test_function_name="test_x",
        target_import="mod",
        target_callable="fn",
        call_expr="target('x')",
        expects="raises",
        expected_exception=None,  # model allows; renderer will reject
    )
    assert s.expects == "raises"
    assert s.expected_exception is None


def test_replace_function_body_qualname_required() -> None:
    with pytest.raises(ValidationError):
        ReplaceFunctionBody(file="a.py", function_qualname="", new_body="pass\n")
