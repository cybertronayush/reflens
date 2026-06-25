"""Resolve raw import tokens to internal file paths, and rank internal centrality.

Import edges are stored as raw tokens (``headroom.transforms``, ``.util``,
``crate::ccr``, ``./foo``). To describe a repo's *architecture* we need to know
which of its OWN modules are most depended-on — not that everyone imports
``pytest``. This resolves tokens against the repo's file set (exact for Python
dotted/relative, best-effort for TS relative + generic key match) and counts
internal dependents.
"""

from __future__ import annotations

from ..store import Database

_STRIP_EXTS = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
               ".go", ".rs", ".java", ".kt", ".rb")


def _index_keys(path: str) -> list[str]:
    p = path
    for ext in _STRIP_EXTS:
        if p.endswith(ext):
            p = p[: -len(ext)]
            break
    parts = [s for s in p.split("/") if s and s not in ("__init__", "mod", "index")]
    if not parts:
        return []
    keys: set[str] = {".".join(parts), "/".join(parts), p}
    # Multi-segment dotted suffixes (a.b.c, b.c) are unambiguous internal refs.
    for i in range(len(parts)):
        suffix = parts[i:]
        if len(suffix) >= 2:
            keys.add(".".join(suffix))
    # A BARE single-segment key is registered ONLY for a top-level module: a bare
    # `import logging` can resolve to a root `logging.py`, never to a deep
    # `a/b/logging.py`. This stops stdlib/common names (logging, json, types,
    # config) on deep internal files from hijacking dependency centrality.
    if len(parts) == 1:
        keys.add(parts[0])
    return [k for k in keys if k]


def build_internal_index(paths: list[str]) -> dict[str, str]:
    """Map every plausible import key to a file path (first occurrence wins)."""
    idx: dict[str, str] = {}
    for p in paths:
        for k in _index_keys(p):
            idx.setdefault(k, p)
    return idx


def resolve_import(token: str, src_path: str, index: dict[str, str], pathset: set[str]) -> str | None:
    t = (token or "").strip()
    if not t:
        return None

    if t.startswith("."):  # relative (python . / .. ; ts ./ ../)
        dots = len(t) - len(t.lstrip("."))
        rest = t[dots:].strip("/")
        base_parts = src_path.split("/")[:-1]
        ups = max(dots - 1, 0)
        if ups:
            base_parts = base_parts[:-ups] if ups <= len(base_parts) else []
        rest_parts = rest.replace(".", "/").split("/") if rest else []
        rel = "/".join([*base_parts, *rest_parts]).strip("/")
        if not rel:
            return None
        for key in (rel, rel.replace("/", ".")):
            if key in index:
                return index[key]
        for ext in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js",
                    ".py", "/__init__.py", ".go", ".rs"):
            cand = rel + ext
            if cand in pathset:
                return cand
        return None

    norm = t.replace("::", ".").replace("/", ".")
    for key in (t, norm, norm.split(".")[0], norm.split(".")[-1]):
        if key in index:
            return index[key]
    return None


def internal_dependents(db: Database) -> dict[str, int]:
    """file path -> number of distinct internal files that import it."""
    paths = [r["path"] for r in db.list_files()]
    pathset = set(paths)
    index = build_internal_index(paths)
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    for e in db.conn.execute("SELECT src, dst FROM edges"):
        tgt = resolve_import(e["dst"], e["src"], index, pathset)
        if not tgt or tgt == e["src"]:
            continue
        pair = (e["src"], tgt)
        if pair in seen:
            continue
        seen.add(pair)
        counts[tgt] = counts.get(tgt, 0) + 1
    return counts
