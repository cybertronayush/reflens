"""Optional tree-sitter outliner (opt-in via REFLENS_USE_TREESITTER=1).

Best-effort, fully guarded enhancer over the regex fallback. Its advantage is
real ``end_line`` spans (regex can't compute scope). Requires the optional
``tree-sitter-language-pack`` extra. Any failure returns None so the registry
falls back to regex — this path is never the sole route to a working outline.
"""

from __future__ import annotations

from .base import ExtractOutput, clip_doc
from ..models import Symbol

# language id -> { tree-sitter node type : symbol kind }
_DEF_NODES: dict[str, dict[str, str]] = {
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "method_definition": "method",
        "generator_function_declaration": "function",
    },
    "typescript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
        "mod_item": "module",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "method_declaration": "method",
        "enum_declaration": "enum",
        "record_declaration": "record",
    },
    "c": {"function_definition": "function", "struct_specifier": "struct"},
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
    },
    "ruby": {"method": "function", "class": "class", "module": "module"},
    "swift": {
        "class_declaration": "class",
        "function_declaration": "function",
        "protocol_declaration": "protocol",
    },
}
_DEF_NODES["tsx"] = _DEF_NODES["typescript"]
_DEF_NODES["jsx"] = _DEF_NODES["javascript"]
_DEF_NODES["kotlin"] = {
    "class_declaration": "class",
    "function_declaration": "function",
    "object_declaration": "object",
}


def try_extract_treesitter(path: str, text: str, lang: str) -> ExtractOutput | None:
    node_map = _DEF_NODES.get(lang)
    if not node_map:
        return None
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
    except Exception:
        return None

    try:
        parser = get_parser(lang)
        data = text.encode("utf-8", errors="replace")
        tree = parser.parse(data)
    except Exception:
        return None

    out = ExtractOutput(extractor=f"tree-sitter:{lang}")
    lines = text.splitlines()

    def name_of(node) -> str | None:
        try:
            field = node.child_by_field_name("name")
            if field is not None:
                return data[field.start_byte:field.end_byte].decode("utf-8", "replace")
        except Exception:
            pass
        for child in getattr(node, "children", []):
            if child.type in ("identifier", "type_identifier", "field_identifier", "constant"):
                return data[child.start_byte:child.end_byte].decode("utf-8", "replace")
        return None

    def walk(node, parent: str | None) -> None:
        kind = node_map.get(node.type)
        new_parent = parent
        if kind:
            nm = name_of(node)
            if nm:
                start = node.start_point[0] + 1
                end = node.end_point[0] + 1
                sig = lines[start - 1].strip() if 0 <= start - 1 < len(lines) else nm
                if len(sig) > 200:
                    sig = sig[:199] + "\u2026"
                out.symbols.append(
                    Symbol(
                        kind=kind, name=nm, signature=sig, parent=parent,
                        start_line=start, end_line=end, docstring=clip_doc(None),
                    )
                )
                if kind in ("class", "impl", "interface", "module", "trait"):
                    new_parent = nm
        for child in getattr(node, "children", []):
            walk(child, new_parent)

    try:
        walk(tree.root_node, None)
    except Exception:
        return None
    return out
