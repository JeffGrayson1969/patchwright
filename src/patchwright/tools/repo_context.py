"""Repo-context retrieval helpers for the patch_plan agent (M2-plan).

extract_symbol_snippet: given a .py file and a function/class name, return the
enclosing definition plus the file's import block. This gives the LLM enough
local context to reason about fix strategies without sending the entire file.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import libcst as cst
from libcst.metadata import QualifiedName, QualifiedNameProvider

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

    Accepts bare names ('my_func') or dotted class-method pairs ('Foo.method').
    Uses QualifiedNameProvider for disambiguation — mirrors codemod_python's
    _ReplaceFunctionBodyTransformer so snippet and patch target the same node.
    Raises SymbolNotFound if the symbol cannot be located.

    The result is capped at *max_lines* lines; if the definition exceeds that,
    only the first *max_lines* lines are returned.
    """
    source = file_path.read_text(encoding="utf-8")
    tree = cst.parse_module(source)

    imports = _collect_imports(tree)
    definition = _find_definition_qnp(tree, symbol)

    snippet_parts = [imports, definition] if imports else [definition]
    snippet = "\n\n".join(p for p in snippet_parts if p)

    lines = snippet.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(f"# ... (truncated at {max_lines} lines)")
    return "\n".join(lines)


def extract_imports_only(file_path: Path) -> str:
    """Return only the import block of *file_path* as a string.

    Used as a fallback when a symbol is not found — gives the LLM scope
    information without leaking unrelated function bodies.
    """
    source = file_path.read_text(encoding="utf-8")
    tree = cst.parse_module(source)
    return _collect_imports(tree)


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


def _find_definition_qnp(tree: cst.Module, symbol: str) -> str:
    """Locate *symbol* using QualifiedNameProvider, matching codemod semantics.

    For bare names ('validate'), matches the module-level definition only.
    For dotted names ('Foo.validate'), matches the class-method form exactly.
    This mirrors _ReplaceFunctionBodyTransformer's qualname matching so the
    snippet and the patch target the same node.
    """
    wrapper = cst.MetadataWrapper(tree)
    collector = _DefinitionCollector(symbol)
    wrapper.visit(collector)
    if collector.result is None:
        raise SymbolNotFound(f"symbol {symbol!r} not found")
    return tree.code_for_node(collector.result).rstrip()


class _DefinitionCollector(cst.CSTVisitor):
    """Visitor that finds the FunctionDef or ClassDef matching *symbol*."""

    METADATA_DEPENDENCIES = (QualifiedNameProvider,)

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.result: cst.FunctionDef | cst.ClassDef | None = None

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if self.result is not None:
            return
        qnames = cast(
            "set[QualifiedName]",
            self.get_metadata(QualifiedNameProvider, node, set()),
        )
        for qn in qnames:
            if self._matches(qn.name):
                self.result = node
                return

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        # Only match bare class names (no dot); method lookup goes via FunctionDef.
        if self.result is not None or "." in self.symbol:
            return
        qnames = cast(
            "set[QualifiedName]",
            self.get_metadata(QualifiedNameProvider, node, set()),
        )
        for qn in qnames:
            if self._matches(qn.name):
                self.result = node
                return

    def _matches(self, qname: str) -> bool:
        # Match exact qualified name or, for bare symbols, the tail after the
        # last dot — consistent with how the codemod resolves simple names.
        if "." in self.symbol:
            return qname == self.symbol or qname.endswith(f".{self.symbol}")
        # Bare symbol: match only when the qualified name is exactly the symbol
        # (module-level) or the tail is the symbol AND there's no intervening
        # class component (i.e., not a method named the same thing).
        # QualifiedNameProvider returns 'validate' for module-level and
        # 'Foo.validate' for a method, so exact match is sufficient here.
        return qname == self.symbol
