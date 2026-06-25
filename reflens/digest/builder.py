"""Assemble the Tier-1 digest data model from the index (+ small blob reads)."""

from __future__ import annotations

import fnmatch
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from ..graph import resolve as _resolve
from ..store import BlobStore, Database

_README_RE = re.compile(r"(^|/)readme(\.[a-z]+)?$", re.IGNORECASE)
_DOC_DIR_RE = re.compile(r"^docs?/", re.IGNORECASE)
_ENTRY_HINTS = (
    "main.py", "__main__.py", "cli.py", "app.py", "server.py", "index.ts",
    "index.js", "main.ts", "main.go", "main.rs", "manage.py", "wsgi.py", "asgi.py",
)
_README_MAX_CHARS = 1800
# Cap how many files get full symbol outlines materialized. The serializer
# truncates by token budget far below this; this just bounds memory on very large
# repos (no point building 100k DigestFile objects to render a few hundred).
_MAX_OUTLINE_FILES = 6000


@dataclass
class DigestSymbol:
    kind: str
    name: str
    signature: str
    parent: Optional[str]
    start_line: int
    docstring: Optional[str] = None


@dataclass
class DigestFile:
    path: str
    lang: str
    line_count: int
    symbols: list[DigestSymbol] = field(default_factory=list)


@dataclass
class Digest:
    meta: dict[str, Any]
    languages: list[dict[str, Any]]
    tree_lines: list[str]
    entry_points: list[str]
    readme_excerpt: Optional[str]
    commit_subjects: list[str]
    doc_titles: list[str]
    files: list[DigestFile]
    total_files: int
    shown_files: int
    modules: list[dict[str, Any]] = field(default_factory=list)
    key_files: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    enrichment: dict[str, str] = field(default_factory=dict)
    enrichment_model: Optional[str] = None
    path_glob: Optional[str] = None


def _strip_markdown_noise(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        # drop badge/image/HTML-only lines common in READMEs
        if s.startswith("<") or s.startswith("![") or s.startswith("[!["):
            continue
        out.append(line)
    # collapse 3+ blank lines
    joined = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def _tree_lines(paths: list[str], *, max_depth: int = 2, max_lines: int = 80) -> list[str]:
    """Compact depth-limited tree with per-directory file counts."""
    # Build nested dir counts.
    dir_files: dict[str, int] = {}
    children: dict[str, set] = {"": set()}
    for p in paths:
        segs = p.split("/")
        for d in range(len(segs)):
            prefix = "/".join(segs[:d])
            node = "/".join(segs[: d + 1])
            children.setdefault(prefix, set()).add(node)
        parent_dir = "/".join(segs[:-1])
        dir_files[parent_dir] = dir_files.get(parent_dir, 0) + 1

    lines: list[str] = []

    def is_dir(node: str) -> bool:
        return node in children and any("/" in c or c != node for c in children[node]) or (
            node in children
        )

    def walk(prefix: str, depth: int) -> None:
        if len(lines) >= max_lines or depth > max_depth:
            return
        for node in sorted(children.get(prefix, [])):
            if len(lines) >= max_lines:
                lines.append("  " * depth + "\u2026")
                return
            name = node.split("/")[-1]
            is_directory = node in children and children.get(node)
            if is_directory:
                count = sum(1 for p in paths if p.startswith(node + "/"))
                lines.append("  " * depth + f"{name}/  ({count})")
                if depth + 1 <= max_depth:
                    walk(node, depth + 1)
            else:
                lines.append("  " * depth + name)

    walk("", 0)
    return lines


def build_digest(
    db: Database,
    blobs: BlobStore,
    *,
    path_glob: Optional[str] = None,
    include_symbols: bool = True,
) -> Digest:
    meta = db.all_meta()
    languages = [dict(r) for r in db.lang_breakdown()]
    all_files = db.list_files()

    selected = [
        r for r in all_files
        if (path_glob is None or fnmatch.fnmatch(r["path"], path_glob))
    ]

    tree = _tree_lines([r["path"] for r in selected])

    entry_points = [
        r["path"] for r in selected
        if r["path"].split("/")[-1] in _ENTRY_HINTS
    ][:20]

    # README excerpt (root-most readme).
    readme_excerpt = None
    readmes = sorted(
        (r for r in all_files if _README_RE.search(r["path"])),
        key=lambda r: r["path"].count("/"),
    )
    if readmes:
        try:
            text = blobs.get_text(readmes[0]["sha256"])
            readme_excerpt = _strip_markdown_noise(text)[:_README_MAX_CHARS]
        except Exception:
            readme_excerpt = None

    doc_titles = []
    for r in all_files:
        if _DOC_DIR_RE.match(r["path"]) and r["path"].lower().endswith((".md", ".mdx", ".rst")):
            doc_titles.append(r["path"])
    doc_titles = doc_titles[:40]

    commit_subjects = meta.get("git_commits", []) or []

    # ---- architecture: modules + internal centrality --------------------
    dependents = _resolve.internal_dependents(db)
    mod_rows: dict[str, list] = defaultdict(list)
    for r in selected:
        top = r["path"].split("/")[0] if "/" in r["path"] else "(root)"
        mod_rows[top].append(r)
    modules: list[dict[str, Any]] = []
    for top, rows in mod_rows.items():
        langs = Counter(rr["lang"] for rr in rows)
        mod_dep = sum(dependents.get(rr["path"], 0) for rr in rows)
        modules.append({
            "name": top,
            "files": len(rows),
            "langs": [lng for lng, _ in langs.most_common(3)],
            "dependents": mod_dep,
            "purpose": _module_purpose(top, db, blobs, all_files),
        })
    modules.sort(key=lambda mm: (-mm["files"], mm["name"]))

    key_files = sorted(
        (
            {"path": p, "dependents": c}
            for p, c in dependents.items()
            if path_glob is None or fnmatch.fnmatch(p, path_glob)
        ),
        key=lambda x: -x["dependents"],
    )[:15]

    # Repo-level signals (skip when scoped to a subtree to keep those fast).
    decisions = _decisions(db, blobs, all_files) if path_glob is None else []
    conventions = _conventions(db) if path_glob is None else []

    files: list[DigestFile] = []
    if include_symbols:
        for r in selected[:_MAX_OUTLINE_FILES]:
            syms = [
                DigestSymbol(
                    kind=s["kind"], name=s["name"], signature=s["signature"],
                    parent=s["parent"], start_line=s["start_line"],
                    docstring=s["docstring"],
                )
                for s in db.symbols_for_file(int(r["id"]))
            ]
            files.append(
                DigestFile(path=r["path"], lang=r["lang"], line_count=r["line_count"], symbols=syms)
            )

    return Digest(
        meta=meta,
        languages=languages,
        tree_lines=tree,
        entry_points=entry_points,
        readme_excerpt=readme_excerpt,
        commit_subjects=commit_subjects[:25],
        doc_titles=doc_titles,
        files=files,
        total_files=len(selected),
        shown_files=len(selected),
        modules=modules,
        key_files=key_files,
        decisions=decisions,
        conventions=conventions,
        enrichment=(meta.get("enrichment", {}) or {}) if path_glob is None else {},
        enrichment_model=meta.get("enrichment_model"),
        path_glob=path_glob,
    )


_DECISION_RE = re.compile(r"(adr|/spec/|proposal|decision|/rfc|realignment|architecture|vision)", re.I)


def _decisions(db: Database, blobs: BlobStore, all_files: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in all_files:
        p = r["path"]
        if not p.lower().endswith((".md", ".mdx", ".rst")):
            continue
        if not _DECISION_RE.search(p):
            continue
        title = None
        frow = db.get_file_by_path(p)
        if frow is not None:
            for s in db.symbols_for_file(int(frow["id"])):
                if s["kind"] == "h1":
                    title = s["name"]
                    break
        summary = None
        try:
            text = _strip_markdown_noise(blobs.get_text(r["sha256"]))
            for para in text.split("\n\n"):
                pp = para.strip().lstrip("#").strip()
                if pp and not pp.startswith("!"):
                    summary = pp[:200]
                    break
        except Exception:
            summary = None
        out.append({"path": p, "title": title, "summary": summary})
        if len(out) >= 20:
            break
    return out


def _conventions(db: Database) -> list[str]:
    out: list[str] = []
    imp = Counter(row["dst"] for row in db.conn.execute("SELECT dst FROM edges"))

    tf = [f"{lbl} ({imp[name]})" for name, lbl in
          (("pytest", "pytest"), ("unittest", "unittest"), ("vitest", "vitest"), ("jest", "jest"))
          if imp.get(name, 0) > 3]
    if tf:
        out.append("Testing: " + ", ".join(tf))

    def _count(sql: str) -> int:
        return int(db.conn.execute(sql).fetchone()["c"])

    exc = _count("SELECT COUNT(*) c FROM symbols WHERE kind='class' "
                 "AND (name LIKE '%Error' OR name LIKE '%Exception')")
    if exc:
        out.append(f"{exc} custom exception classes (typed errors)")

    total_fn = _count("SELECT COUNT(*) c FROM symbols WHERE kind IN ('function','method')")
    typed = _count("SELECT COUNT(*) c FROM symbols WHERE kind IN ('function','method') "
                   "AND signature LIKE '%-> %'")
    if total_fn:
        out.append(f"Return type hints on {100 * typed // total_fn}% of functions/methods")

    asy = _count("SELECT COUNT(*) c FROM symbols WHERE signature LIKE 'async def%' "
                 "OR signature LIKE '%@% async def%'")
    if asy > 5:
        out.append(f"{asy} async functions (async-heavy)")

    for name, lbl in (("dataclasses", "dataclasses"), ("pydantic", "pydantic models"),
                      ("attrs", "attrs"), ("zod", "zod schemas")):
        if imp.get(name, 0) > 3:
            out.append(f"{lbl} ({imp[name]} modules)")
    return out


def _module_purpose(
    top: str, db: Database, blobs: BlobStore, all_files: list
) -> Optional[str]:
    """One-line purpose for a top-level module: prefer its __init__ module
    docstring, then a README first paragraph, then the package's first file doc."""
    init_path = f"{top}/__init__.py"
    frow = db.get_file_by_path(init_path)
    if frow is not None:
        for s in db.symbols_for_file(int(frow["id"])):
            if s["kind"] == "module" and s["docstring"]:
                return s["docstring"]
    for r in all_files:
        p = r["path"]
        if p.startswith(f"{top}/") and _README_RE.search(p):
            try:
                text = _strip_markdown_noise(blobs.get_text(r["sha256"]))
                for para in text.split("\n\n"):
                    para = para.strip().lstrip("#").strip()
                    if para and not para.startswith("!"):
                        return para[:200]
            except Exception:
                return None
    return None
