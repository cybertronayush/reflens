"""Neighbor expansion: what a file/symbol imports, and what imports it.

Edges are stored as (src_file_path -> raw_import_token). Outgoing edges are
exact. Incoming edges are resolved heuristically by matching a file's plausible
module keys against import tokens (exact for Python-style imports, best-effort
otherwise). This is the "expand into related context" seam for the agent.
"""

from __future__ import annotations

from typing import Any

from ..store import Database


def _module_keys(path: str) -> list[str]:
    """Plausible import tokens that would resolve to this file."""
    p = path
    for ext in (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"):
        if p.endswith(ext):
            p = p[: -len(ext)]
            break
    parts = [seg for seg in p.split("/") if seg and seg != "__init__"]
    keys: set[str] = set()
    if parts:
        keys.add(parts[-1])  # bare module name
        keys.add(".".join(parts))  # dotted
        keys.add("/".join(parts))  # path-ish
        keys.add(p)
        # trailing dotted suffixes (a.b.c, b.c, c)
        for i in range(len(parts)):
            keys.add(".".join(parts[i:]))
    return [k for k in keys if k]


def neighbors(db: Database, target: str, *, limit: int = 50) -> dict[str, Any]:
    target = target.strip()
    file_row = db.get_file_by_path(target)

    if file_row is None:
        syms = db.find_symbols_by_name(target, limit=limit)
        if syms:
            return {
                "target": target,
                "kind": "symbol",
                "definitions": [
                    {
                        "path": s["path"],
                        "symbol": s["name"],
                        "decl": s["signature"],
                        "kind": s["kind"],
                        "start_line": s["start_line"],
                        "end_line": s["end_line"],
                    }
                    for s in syms
                ],
            }
        return {"target": target, "kind": "unknown", "error": "no file or symbol with that name"}

    path = file_row["path"]
    out_edges = db.edges_from(path)
    imports = sorted({e["dst"] for e in out_edges})

    keys = set(_module_keys(path))
    imported_by: set[str] = set()
    # Targeted scan: pull edges whose dst could reference this file.
    for key in list(keys)[:12]:
        for e in db.conn.execute(
            "SELECT DISTINCT src FROM edges WHERE dst = ? OR dst LIKE ? LIMIT ?",
            (key, f"%{key}", limit),
        ):
            if e["src"] != path:
                imported_by.add(e["src"])

    defines = [
        {"kind": s["kind"], "name": s["name"], "decl": s["signature"], "start_line": s["start_line"]}
        for s in db.symbols_for_file(int(file_row["id"]))
    ]

    return {
        "target": path,
        "kind": "file",
        "lang": file_row["lang"],
        "imports": imports[:limit],
        "imported_by": sorted(imported_by)[:limit],
        "defines": defines[:limit],
    }
