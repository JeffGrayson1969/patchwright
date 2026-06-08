"""Repo-context retrieval helpers for the patch_plan agent (M2-plan).

extract_symbol_snippet: given a .py file and a function/class name, return the
enclosing definition plus the file's import block. This gives the LLM enough
local context to reason about fix strategies without sending the entire file.
"""

from __future__ import annotations

from pathlib import Path

import libcst as cst

# 200 LoC cap: covers realistic function bodies while staying well under a
# single-turn token budget for any major provider at the time of writing.
DEFAULT_MAX_LINES = 200


class SymbolNotFound(ValueError):
    """Raised when the requested symbol is absent from the parsed module."""


def extract_symbol_snippet(
    file_path: Path,
    symbol: str,
    max_lines: int = DEFAULT_MAX_LINES,
) -> str:
    """Return the import block + enclosing function/class for *symbol*.

    Searches module-level definitions first, then one level deep inside classes
    (handles `ClassName.method` as well as bare `method`). Raises SymbolNotFound
    if the symbol cannot be located.

    The result is capped at *max_lines* lines; if the definition exceeds that,
    only the first *max_lines* lines are returned (enough for LLM context).
    """
    source = file_path.read_text(encoding="utf-8")
    tree = cst.parse_module(source)

    imports = _collect_imports(tree)
    definition = _find_definition(tree, symbol)

    snippet_parts = [imports, definition] if imports else [definition]
    snippet = "\n\n".join(p for p in snippet_parts if p)

    lines = snippet.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(f"# ... (truncated at {max_lines} lines)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- internals


def _collect_imports(tree: cst.Module) -> str:
    """Extract all import statements from the module into a single block."""
    lines: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, cst.SimpleStatementLine):
            for inner in stmt.body:
                if isinstance(inner, (cst.Import, cst.ImportFrom)):
                    lines.append(tree.code_for_node(stmt).rstrip())
                    break
    return "\n".join(lines)


def _find_definition(tree: cst.Module, symbol: str) -> str:
    """Locate *symbol* in the module and return its source text.

    Accepts bare names ('my_func') or dotted class-method pairs ('MyClass.method').
    """
    if "." in symbol:
        class_name, _, method_name = symbol.partition(".")
        return _find_method(tree, class_name, method_name)
    return _find_top_level(tree, symbol)


def _find_top_level(tree: cst.Module, name: str) -> str:
    """Return source for a module-level function or class named *name*."""
    for stmt in tree.body:
        if isinstance(stmt, (cst.FunctionDef, cst.ClassDef)) and _def_name(stmt) == name:
            return tree.code_for_node(stmt).rstrip()
    raise SymbolNotFound(f"symbol {name!r} not found in {tree}")


def _find_method(tree: cst.Module, class_name: str, method_name: str) -> str:
    """Return source for a method inside a named class."""
    for stmt in tree.body:
        if isinstance(stmt, cst.ClassDef) and _def_name(stmt) == class_name:
            for item in stmt.body.body:
                if isinstance(item, cst.FunctionDef) and _def_name(item) == method_name:
                    return tree.code_for_node(item).rstrip()
            raise SymbolNotFound(f"method {method_name!r} not found in class {class_name!r}")
    raise SymbolNotFound(f"class {class_name!r} not found in module")


def _def_name(node: cst.FunctionDef | cst.ClassDef) -> str:
    return node.name.value
