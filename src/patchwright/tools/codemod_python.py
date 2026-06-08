# Applies a Pydantic-validated PatchPlan to source via LibCST (FR-PT-1 Phase B).
# T1 mitigation lives in the mandatory human-review gate (CLAUDE.md #8), not here
# — this layer assumes the plan has been reviewed before write_modified() is called.

from __future__ import annotations

import difflib
from pathlib import Path
from typing import cast

import libcst as cst
from libcst.metadata import QualifiedName, QualifiedNameProvider

from patchwright.models.patch_plan import (
    AddTestCase,
    InsertImport,
    PatchOperation,
    PatchPlan,
    ReplaceFunctionBody,
    WrapCallWithValidator,
)


class CodemodError(Exception):
    """Raised when an operation's preconditions are not met or apply fails."""


# --------------------------------------------------------------------------- public


def apply(plan: PatchPlan, repo_root: Path) -> dict[Path, str]:
    """Apply every operation in `plan` against `repo_root`. Returns the new
    contents of every file the plan touched, keyed by absolute path.

    Pure: does NOT write to disk. Use `write_modified()` to persist.
    """
    repo_root = repo_root.resolve()
    file_sources: dict[Path, str] = {}

    for op in plan.operations:
        target = _resolve_path(repo_root, op.file)
        if target not in file_sources:
            file_sources[target] = _read_initial(target, op)
        file_sources[target] = _apply_one(op, file_sources[target])

    return file_sources


def diff(repo_root: Path, modified: dict[Path, str]) -> str:
    """Produce a unified diff for the modified files. Original is read from
    disk; missing originals (newly-created files) compare against empty."""
    repo_root = repo_root.resolve()
    chunks: list[str] = []
    for path, new_source in sorted(modified.items()):
        rel = path.relative_to(repo_root).as_posix()
        try:
            original = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            original = ""
        if original == new_source:
            continue
        chunk = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        chunks.append("".join(chunk))
    return "".join(chunks)


def write_modified(modified: dict[Path, str]) -> None:
    """Persist the result of `apply()` to disk. Creates parent dirs as needed."""
    for path, new_source in modified.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_source, encoding="utf-8")


# --------------------------------------------------------------------------- internals


def _resolve_path(repo_root: Path, relpath: str) -> Path:
    """Join + reject path traversal. The plan can be LLM-emitted, so distrust it."""
    candidate = (repo_root / relpath).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise CodemodError(f"path escapes repo root: {relpath!r}") from exc
    return candidate


def _read_initial(path: Path, op: PatchOperation) -> str:
    """Read the file's current contents. For AddTestCase against a missing file,
    return an empty module skeleton; all other ops require the file to exist."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if isinstance(op, AddTestCase):
            return "from __future__ import annotations\n\n"
        raise CodemodError(f"target file does not exist: {path}") from None


def _apply_one(op: PatchOperation, source: str) -> str:
    if isinstance(op, InsertImport):
        return _apply_insert_import(op, source)
    if isinstance(op, ReplaceFunctionBody):
        return _apply_replace_function_body(op, source)
    if isinstance(op, WrapCallWithValidator):
        return _apply_wrap_call(op, source)
    if isinstance(op, AddTestCase):
        return _apply_add_test_case(op, source)
    # Exhaustive match — discriminated union is closed.
    raise CodemodError(f"unknown PatchOperation type: {type(op).__name__}")  # pragma: no cover


# --------------------------------------------------------------------------- ops


def _apply_insert_import(op: InsertImport, source: str) -> str:
    tree = cst.parse_module(source)
    if _has_import(tree, op):
        return source  # idempotent

    if op.names:
        names = [cst.ImportAlias(name=_dotted_name(n)) for n in op.names]
        import_stmt = cst.SimpleStatementLine(
            body=[cst.ImportFrom(module=_dotted_name(op.module), names=names)]
        )
    else:
        import_stmt = cst.SimpleStatementLine(
            body=[cst.Import(names=[cst.ImportAlias(name=_dotted_name(op.module))])]
        )

    new_body = _insert_after_module_docstring(tree, import_stmt)
    return tree.with_changes(body=new_body).code


def _has_import(tree: cst.Module, op: InsertImport) -> bool:
    """True if `tree` already imports what `op` would add (or a superset)."""
    target_module = op.module
    target_names = set(op.names or [])
    for stmt in tree.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for inner in stmt.body:
            if op.names is None and isinstance(inner, cst.Import):
                for alias in inner.names:
                    if _dotted_name_to_str(alias.name) == target_module:
                        return True
            elif op.names is not None and isinstance(inner, cst.ImportFrom):
                if inner.module is None:
                    continue
                if _dotted_name_to_str(inner.module) != target_module:
                    continue
                if isinstance(inner.names, cst.ImportStar):
                    return True
                existing = {_alias_target(a) for a in inner.names}
                if target_names.issubset(existing):
                    return True
    return False


def _alias_target(alias: cst.ImportAlias) -> str:
    """The local name introduced by an alias (asname if present, else name)."""
    if alias.asname is not None:
        name = alias.asname.name
        if isinstance(name, cst.Name):
            return name.value
    return _dotted_name_to_str(alias.name)


def _insert_after_module_docstring(
    tree: cst.Module, new_stmt: cst.SimpleStatementLine
) -> tuple[cst.BaseStatement, ...]:
    """Place a new statement after the module docstring + `from __future__`
    imports, before any other code. Adds a PEP-8 blank line before the inserted
    import (separating future imports from other imports)."""
    body = list(tree.body)
    insert_at = 0

    # Skip module docstring (a single SimpleString expression at top).
    if body and isinstance(body[0], cst.SimpleStatementLine):
        first = body[0].body[0]
        if isinstance(first, cst.Expr) and isinstance(first.value, cst.SimpleString):
            insert_at = 1

    # Skip `from __future__ import …` lines.
    while insert_at < len(body):
        stmt = body[insert_at]
        if isinstance(stmt, cst.SimpleStatementLine) and stmt.body:
            inner = stmt.body[0]
            if (
                isinstance(inner, cst.ImportFrom)
                and inner.module is not None
                and _dotted_name_to_str(inner.module) == "__future__"
            ):
                insert_at += 1
                continue
        break

    # Add a blank line above the inserted import if we skipped past either a
    # docstring or future-imports — to separate sections per PEP 8.
    if insert_at > 0:
        new_stmt = new_stmt.with_changes(leading_lines=(cst.EmptyLine(),))

    body.insert(insert_at, new_stmt)
    return tuple(body)


def _apply_replace_function_body(op: ReplaceFunctionBody, source: str) -> str:
    new_body_module = cst.parse_module(op.new_body)
    new_indented = cst.IndentedBlock(body=tuple(new_body_module.body))

    wrapper = cst.MetadataWrapper(cst.parse_module(source))
    transformer = _ReplaceFunctionBodyTransformer(op.function_qualname, new_indented)
    new_tree = wrapper.visit(transformer)
    if not transformer.matched:
        raise CodemodError(f"function not found in {op.file!r}: {op.function_qualname!r}")
    return new_tree.code


def _apply_wrap_call(op: WrapCallWithValidator, source: str) -> str:
    wrapper = cst.MetadataWrapper(cst.parse_module(source))
    transformer = _WrapCallTransformer(op.call_name, op.wrapper, op.arg_index)
    new_tree = wrapper.visit(transformer)
    if transformer.candidates == 0:
        raise CodemodError(
            f"no calls to {op.call_name!r} (arg_index {op.arg_index}) in {op.file!r}"
        )
    return new_tree.code


def _apply_add_test_case(op: AddTestCase, source: str) -> str:
    tree = cst.parse_module(source)

    # Reject duplicate function names.
    for stmt in tree.body:
        if isinstance(stmt, cst.FunctionDef) and stmt.name.value == op.test_function_name:
            raise CodemodError(f"test function already exists: {op.test_function_name!r}")

    body_module = cst.parse_module(op.test_body)
    func = cst.FunctionDef(
        name=cst.Name(op.test_function_name),
        params=cst.Parameters(),
        body=cst.IndentedBlock(body=tuple(body_module.body)),
    )
    # Add two blank lines before the new function for PEP 8 spacing.
    leading_lines: tuple[cst.EmptyLine, ...] = (cst.EmptyLine(), cst.EmptyLine())
    func = func.with_changes(leading_lines=leading_lines)
    return tree.with_changes(body=(*tree.body, func)).code


# --------------------------------------------------------------------------- helpers


def _dotted_name(name: str) -> cst.Attribute | cst.Name:
    parts = name.split(".")
    node: cst.Attribute | cst.Name = cst.Name(parts[0])
    for part in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node


def _dotted_name_to_str(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return f"{_dotted_name_to_str(node.value)}.{node.attr.value}"
    return ""


# --------------------------------------------------------------------------- transformers


class _ReplaceFunctionBodyTransformer(cst.CSTTransformer):
    """Replace the body of one function identified by dotted qualname.

    Handles module-level and one-level-nested-in-class names. Sets
    `self.matched` so the caller can raise on miss.
    """

    METADATA_DEPENDENCIES = (QualifiedNameProvider,)

    def __init__(self, qualname: str, new_body: cst.IndentedBlock) -> None:
        super().__init__()
        self.qualname = qualname
        self.new_body = new_body
        self.matched = False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        qualnames = cast(
            "set[QualifiedName]",
            self.get_metadata(QualifiedNameProvider, original_node, set()),
        )
        for qn in qualnames:
            # libcst returns 'module.FunctionDef' or 'module.Class.method'. We
            # accept either the unqualified tail or the dotted-class form.
            simple = qn.name.split(".", 1)[-1]
            if self.qualname in (simple, qn.name):
                self.matched = True
                return updated_node.with_changes(body=self.new_body)
        return updated_node


class _WrapCallTransformer(cst.CSTTransformer):
    """Wrap the `arg_index`-th positional argument of every matching call.

    `candidates` counts call sites with the right name AND enough positional
    args; `matches` counts sites we actually rewrote (excludes already-wrapped).
    Raising on `candidates == 0` lets idempotent runs succeed.
    """

    def __init__(self, call_name: str, wrapper: str, arg_index: int) -> None:
        super().__init__()
        self.call_name = call_name
        self.wrapper = wrapper
        self.arg_index = arg_index
        self.candidates = 0
        self.matches = 0

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        if not _call_name_matches(updated_node, self.call_name):
            return updated_node

        if len(updated_node.args) <= self.arg_index:
            return updated_node

        self.candidates += 1
        target_arg = updated_node.args[self.arg_index]

        # Skip if already wrapped with our wrapper.
        if _is_call_to(target_arg.value, self.wrapper):
            return updated_node

        new_value = cst.Call(
            func=_dotted_name(self.wrapper),
            args=[cst.Arg(value=target_arg.value)],
        )
        new_arg = target_arg.with_changes(value=new_value)
        new_args = list(updated_node.args)
        new_args[self.arg_index] = new_arg
        self.matches += 1
        return updated_node.with_changes(args=new_args)


def _call_name_matches(call: cst.Call, name: str) -> bool:
    return _dotted_name_to_str(call.func) == name


def _is_call_to(node: cst.BaseExpression, name: str) -> bool:
    return isinstance(node, cst.Call) and _dotted_name_to_str(node.func) == name
