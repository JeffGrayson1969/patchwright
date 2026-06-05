"""Generate a regression-test file from a PatchPlan.TestSpec (FR-PT-2).

The generated test:
  1. Imports the patched callable.
  2. Runs `setup_code` (optional).
  3. Calls the callable via `call_expr` with the malicious input.
  4. Asserts the post-patch behavior specified by `expects`.

Two-phase guarantee: this generator is fully deterministic. It uses LibCST
to build the AST so we cannot accidentally inject arbitrary code via string
formatting of the spec fields — every spec field flows into a typed CST node.
"""

from __future__ import annotations

import libcst as cst

from patchwright.models.patch_plan import TestSpec


def render(spec: TestSpec) -> str:
    """Return the Python source for the test file."""
    body: list[cst.SimpleStatementLine | cst.BaseCompoundStatement] = [
        cst.SimpleStatementLine(
            body=[
                cst.ImportFrom(
                    module=cst.Name("__future__"),
                    names=[cst.ImportAlias(name=cst.Name("annotations"))],
                )
            ]
        ),
    ]

    if spec.expects == "raises":
        body.append(
            cst.SimpleStatementLine(
                body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("pytest"))])]
            )
        )

    body.append(
        cst.SimpleStatementLine(
            body=[
                cst.ImportFrom(
                    module=_dotted(spec.target_import),
                    names=[
                        cst.ImportAlias(
                            name=cst.Name(spec.target_callable),
                            asname=cst.AsName(name=cst.Name("target")),
                        )
                    ],
                )
            ]
        )
    )

    test_body = _build_test_body(spec)
    func = cst.FunctionDef(
        name=cst.Name(spec.test_function_name),
        params=cst.Parameters(),
        body=cst.IndentedBlock(body=test_body),
        leading_lines=(cst.EmptyLine(), cst.EmptyLine()),
    )
    body.append(func)

    return cst.Module(body=body).code


def _build_test_body(spec: TestSpec) -> tuple[cst.BaseStatement, ...]:
    """Construct the test function body. Each input field is parsed (not
    f-string-interpolated), so a malicious spec cannot inject extra code."""
    stmts: list[cst.BaseStatement] = []

    if spec.setup_code.strip():
        setup_module = cst.parse_module(spec.setup_code)
        stmts.extend(setup_module.body)

    if spec.expects == "raises":
        if not spec.expected_exception:
            raise ValueError("TestSpec.expects='raises' requires expected_exception to be set")
        # with pytest.raises(<ExceptionClass>):
        #     <call_expr>
        with_body = cst.parse_module(spec.call_expr).body
        raises_with = cst.With(
            items=[
                cst.WithItem(
                    item=cst.Call(
                        func=cst.Attribute(value=cst.Name("pytest"), attr=cst.Name("raises")),
                        args=[cst.Arg(value=_dotted(spec.expected_exception))],
                    )
                )
            ],
            body=cst.IndentedBlock(body=with_body),
        )
        stmts.append(raises_with)
        return tuple(stmts)

    # expects in {"returns_none", "returns_empty"}: bind result, assert.
    call_module = cst.parse_module(spec.call_expr)
    if len(call_module.body) != 1 or not isinstance(call_module.body[0], cst.SimpleStatementLine):
        raise ValueError("TestSpec.call_expr must be exactly one expression statement")
    inner = call_module.body[0].body[0]
    if not isinstance(inner, cst.Expr):
        raise ValueError("TestSpec.call_expr must be an expression, not a statement")

    result_assign = cst.SimpleStatementLine(
        body=[
            cst.Assign(
                targets=[cst.AssignTarget(target=cst.Name("result"))],
                value=inner.value,
            )
        ]
    )
    stmts.append(result_assign)

    if spec.expects == "returns_none":
        assertion = cst.SimpleStatementLine(
            body=[
                cst.Assert(
                    test=cst.Comparison(
                        left=cst.Name("result"),
                        comparisons=[
                            cst.ComparisonTarget(operator=cst.Is(), comparator=cst.Name("None"))
                        ],
                    )
                )
            ]
        )
    else:  # returns_empty
        # assert not result
        assertion = cst.SimpleStatementLine(
            body=[
                cst.Assert(
                    test=cst.UnaryOperation(operator=cst.Not(), expression=cst.Name("result"))
                )
            ]
        )
    stmts.append(assertion)
    return tuple(stmts)


def _dotted(name: str) -> cst.Attribute | cst.Name:
    parts = name.split(".")
    node: cst.Attribute | cst.Name = cst.Name(parts[0])
    for part in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node
