"""Assemble the Tier-1 digest data model from the index (+ small blob reads)."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..store import BlobStore, Database

_README_RE = re.compile(r"(^|/)readme(\.[a-z]+)?$", re.IGNORECASE)
_DOC_DIR_RE = re.compile(r"^docs?/", re.IGNORECASE)
_ENTRY_HINTS = (
    "main.py", "__main__.py", "cli.py", "app.py", "server.py", "index.ts",
    "index.js", "main.ts", "main.go", "main.rs", "manage.py", "wsgi.py", "asgi.py",
)
_README_MAX_CHARS = 1800


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

    files: list[DigestFile] = []
    if include_symbols:
        for r in selected:
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
    )
