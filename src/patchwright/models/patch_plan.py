"""PatchPlan — the deterministic-application contract (FR-PT-1 Phase B).

The patch_plan agent (Wave B, M2-plan) emits a PatchPlan via the LLM. The
codemod (this PR, M2-codemod) applies it deterministically with LibCST.
The LLM never writes file mutations directly — it emits a typed plan and
the codemod is responsible for everything that touches disk.

This split is the foundation of the T1 mitigation (PRD §9 — "malicious
patch via poisoned report"): the codemod refuses risky operations (network
exfil insertion, eval, dynamic imports), enforces operation pre-conditions
(target function exists, target file is Python), and produces a unified
diff that a human reviewer reads before any PR opens.

Operations form a discriminated union so the schema is self-describing
both to the LLM (via response_schema for the agent) and to mypy.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- ops


class InsertImport(BaseModel):
    """Add `import x` or `from x import y, z` to a file's top-of-module imports.

    Idempotent: if the import already exists, the codemod is a no-op.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["insert_import"] = "insert_import"

    file: str = Field(
        description="Repo-relative POSIX path to the .py file to modify.",
    )
    module: str = Field(
        min_length=1,
        description="The module path. For `import os.path`, this is 'os.path'.",
    )
    names: list[str] | None = Field(
        default=None,
        description=(
            "If set, emit `from {module} import {names}`. If None, emit `import {module}`."
        ),
    )


class ReplaceFunctionBody(BaseModel):
    """Replace the body of a named function with new source.

    `function_qualname` is dot-separated: 'module_level_fn' or 'ClassName.method'.
    The codemod parses `new_body` as Python and substitutes it; it does NOT
    splice raw source, so syntactically-invalid new_body fails fast.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["replace_function_body"] = "replace_function_body"

    file: str
    function_qualname: str = Field(
        min_length=1,
        description="Dotted path to the function. 'foo' or 'ClassName.method'.",
    )
    new_body: str = Field(
        min_length=1,
        description="The new function body as Python source (without 'def ...:' header).",
    )


class WrapCallWithValidator(BaseModel):
    """Wrap one argument of every call to `call_name` with `wrapper(...)`.

    Example: `open(path)` → `open(safe_path(path))` is
    WrapCallWithValidator(call_name='open', wrapper='safe_path', arg_index=0).

    The codemod only wraps positional arguments at the given index. If the
    target argument is already wrapped with `wrapper`, the call is left alone
    (idempotent).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["wrap_call_with_validator"] = "wrap_call_with_validator"

    file: str
    call_name: str = Field(
        min_length=1,
        description="Function being called, e.g. 'open' or 'subprocess.run'.",
    )
    wrapper: str = Field(
        min_length=1,
        description="Wrapper function to apply, e.g. 'safe_path'.",
    )
    arg_index: int = Field(
        default=0,
        ge=0,
        description="Zero-based positional argument to wrap.",
    )


class AddTestCase(BaseModel):
    """Append a test function to a Python test file.

    If the file does not exist yet, the codemod creates it with the standard
    `from __future__ import annotations` header. If a function with the same
    name already exists, the operation fails (the planning agent must choose
    a unique name).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["add_test_case"] = "add_test_case"

    file: str
    test_function_name: str = Field(
        min_length=1,
        pattern=r"^test_[A-Za-z0-9_]+$",
        description="Must start with 'test_'.",
    )
    test_body: str = Field(
        min_length=1,
        description="The test function body as Python source (without 'def ...:' header).",
    )


PatchOperation = Annotated[
    InsertImport | ReplaceFunctionBody | WrapCallWithValidator | AddTestCase,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------- test spec


class TestSpec(BaseModel):
    """Regression-test specification driven by `test_gen_python.py`.

    The generated test imports the patched code, calls `setup_code` to
    construct an input that demonstrates the vulnerability, and asserts that
    the patched behavior raises or returns safely (per `expects`).
    """

    # Tell pytest this is a Pydantic model, not a test class.
    __test__: ClassVar[bool] = False

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str = Field(
        description="Repo-relative path to write the test file at (must start with 'tests/').",
    )

    test_function_name: str = Field(
        min_length=1,
        pattern=r"^test_[A-Za-z0-9_]+$",
    )

    target_import: str = Field(
        description="Module to import the patched function from, e.g. 'myapp.users'.",
    )

    target_callable: str = Field(
        description="Callable name within target_import, e.g. 'get_user'.",
    )

    setup_code: str = Field(
        default="",
        description="Optional Python statements to run before invoking the callable.",
    )

    call_expr: str = Field(
        description=(
            "Python expression that calls the patched function with the malicious "
            "input. Available as `target` (the imported callable) in the test scope."
        ),
    )

    expects: Literal["raises", "returns_none", "returns_empty"] = Field(
        description="What the patched code should do with the malicious input.",
    )

    expected_exception: str | None = Field(
        default=None,
        description="Required iff expects='raises'. Exception class name, e.g. 'ValueError'.",
    )


# --------------------------------------------------------------------------- plan


class PatchPlan(BaseModel):
    """A typed, fully-deterministic patch description.

    M2-plan (Wave B) populates this via an LLM call with response_schema.
    The codemod applies it. The orchestrator persists it as an artifact;
    M2-pr opens a PR from the diff.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"

    case_id: str = Field(description="Case id this plan addresses.")

    summary: str = Field(
        min_length=1,
        max_length=200,
        description="One-line description of the fix. Shown in commit message + PR title.",
    )

    operations: list[PatchOperation] = Field(
        min_length=1,
        description="Ordered list of patch operations. Applied in order.",
    )

    test_spec: TestSpec | None = Field(
        default=None,
        description="Regression test for the fix. Generated by test_gen_python.py.",
    )

    rationale: str = Field(
        min_length=1,
        max_length=4000,
        description="Why this patch fixes the vulnerability. Read by the human reviewer.",
    )
