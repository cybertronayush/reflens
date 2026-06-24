"""Exact Python outline via the stdlib ``ast`` module (zero dependencies).

Captures module-level classes/functions, class methods, ALL-CAPS module
constants, and import statements. Signatures are reconstructed one-line; the body
is never included (that lives losslessly in Tier 2).
"""

from __future__ import annotations

import ast

from ..models import Symbol
from .base import ExtractOutput, clip_doc


def _func_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    try:
        args = ast.unparse(node.args)
    except Exception:
        args = "..."
    ret = ""
    if node.returns is not None:
        try:
            ret = f" -> {ast.unparse(node.returns)}"
        except Exception:
            ret = ""
    deco = ""
    if node.decorator_list:
        names = []
        for d in node.decorator_list:
            try:
                names.append("@" + ast.unparse(d))
            except Exception:
                continue
        if names:
            deco = " ".join(names) + " "
    return f"{deco}{prefix}{node.name}({args}){ret}"


def _class_signature(node: ast.ClassDef) -> str:
    parts: list[str] = []
    for b in node.bases:
        try:
            parts.append(ast.unparse(b))
        except Exception:
            continue
    for k in node.keywords:
        try:
            parts.append(ast.unparse(k))
        except Exception:
            continue
    inside = ", ".join(parts)
    return f"class {node.name}({inside})" if inside else f"class {node.name}"


def _end_line(node: ast.AST, fallback: int) -> int:
    return int(getattr(node, "end_lineno", None) or fallback)


def extract_python(path: str, text: str, lang: str = "python") -> ExtractOutput:
    out = ExtractOutput(extractor="python-ast")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Unparseable (py2, partial, template). Fall back to import-only scan so the
        # graph still gets edges; symbols stay empty rather than wrong.
        out.extractor = "python-ast(syntaxerror)"
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                out.imports.extend(_imports_from_source_line(s))
        return out

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.imports.extend(_imports_from_node(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.symbols.append(
                Symbol(
                    kind="function",
                    name=node.name,
                    signature=_func_signature(node),
                    start_line=node.lineno,
                    end_line=_end_line(node, node.lineno),
                    docstring=clip_doc(ast.get_docstring(node)),
                )
            )
        elif isinstance(node, ast.ClassDef):
            out.symbols.append(
                Symbol(
                    kind="class",
                    name=node.name,
                    signature=_class_signature(node),
                    start_line=node.lineno,
                    end_line=_end_line(node, node.lineno),
                    docstring=clip_doc(ast.get_docstring(node)),
                )
            )
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.symbols.append(
                        Symbol(
                            kind="method",
                            name=sub.name,
                            signature=_func_signature(sub),
                            parent=node.name,
                            start_line=sub.lineno,
                            end_line=_end_line(sub, sub.lineno),
                            docstring=clip_doc(ast.get_docstring(sub)),
                        )
                    )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _const_names(node):
                out.symbols.append(
                    Symbol(
                        kind="const",
                        name=name,
                        signature=name,
                        start_line=node.lineno,
                        end_line=_end_line(node, node.lineno),
                    )
                )
    return out


def _const_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Module-level ALL_CAPS names — strong config/constant signal, low noise."""
    names: list[str] = []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for t in targets:
        if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1:
            names.append(t.id)
    return names


def _imports_from_node(node: ast.Import | ast.ImportFrom) -> list[str]:
    mods: list[str] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            mods.append(alias.name)
    else:  # ImportFrom
        base = ("." * (node.level or 0)) + (node.module or "")
        if base:
            mods.append(base)
    return mods


def _imports_from_source_line(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("from "):
        try:
            return [line.split()[1]]
        except IndexError:
            return []
    if line.startswith("import "):
        rest = line[len("import "):]
        return [p.strip().split(" as ")[0].split(".")[0] for p in rest.split(",") if p.strip()]
    return []
