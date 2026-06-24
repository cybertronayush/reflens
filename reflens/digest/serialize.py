"""Render a Digest to dense markdown within a token budget.

Resolution levels:
  0 - brief only (meta, language mix, tree, entry points, intent)
  1 - brief + per-file outlines, top-level symbols (classes/functions/types)
  2 - brief + per-file outlines, all symbols including methods

The budget is always honored. When per-file outlines don't all fit, output stops
and emits an explicit drill-down pointer listing the remaining files and the
exact tools to reach them — so truncation never means loss.
"""

from __future__ import annotations

from typing import Any

from ..tokenizer import estimate_tokens, is_accurate
from .builder import Digest, DigestFile

_RESERVE_TOKENS = 1500


def _human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.0f}{u}" if u == "B" else f"{f:.1f}{u}"
        f /= 1024
    return f"{n}B"


def _brief(d: Digest, level: int, budget: int) -> str:
    m = d.meta
    lines: list[str] = []
    name = m.get("name", "repo")
    lines.append(f"# Reference repo: {name}")
    lines.append("")
    lines.append(
        "> Tier-1 Intelligence Digest. Lossy on implementation bodies, complete on "
        "structure & public surface. The full byte-exact source is in Tier 2 — use "
        "`reflens_read` for any file/symbol, `reflens_search` to find code, "
        "`reflens_neighbors` to expand dependencies."
    )
    lines.append("")
    src = f"{m.get('source_kind','?')}: {m.get('source_ref','?')}"
    commit = m.get("commit_sha")
    meta_bits = [
        f"source: {src}",
        f"files indexed: {m.get('file_count', '?')}",
        f"symbols: {m.get('symbol_count', '?')}",
        f"chunks: {m.get('chunk_count', '?')}",
    ]
    declared = m.get("declared_file_count")
    if declared is not None and declared != m.get("file_count"):
        meta_bits.append(f"declared: {declared}")
    if commit:
        meta_bits.append(f"commit: {commit[:12]}")
    if m.get("ingested_at"):
        meta_bits.append(f"ingested: {m['ingested_at'][:19]}Z")
    lines.append("  ·  ".join(meta_bits))
    cfg = m.get("config", {}) or {}
    if cfg.get("repomix_transforms"):
        lines.append(
            f"\n> NOTE: source is a Repomix dump with transforms "
            f"({', '.join(cfg['repomix_transforms'])}). Content is lossless wrt the dump, "
            "not the original repo."
        )
    if cfg.get("semantic"):
        lines.append("\n> Semantic (vector) search is enabled for this repo.")

    # Language mix
    if d.languages:
        lines.append("\n## Language mix")
        lines.append("| lang | files | lines | size |")
        lines.append("|---|--:|--:|--:|")
        for r in d.languages[:14]:
            lines.append(
                f"| {r['lang']} | {r['files']} | {r['lines'] or 0} | {_human_bytes(r['bytes'] or 0)} |"
            )

    # Files present but not indexed (binaries / excluded from a dump).
    excluded = m.get("excluded_files") or []
    if excluded:
        lines.append(f"\n## Present but not indexed ({len(excluded)})")
        lines.append("In the repo but no content available (binary/excluded):")
        lines.append(", ".join(f"`{p}`" for p in excluded[:40]))
        if len(excluded) > 40:
            lines.append(f"…and {len(excluded) - 40} more.")

    # Entry points
    if d.entry_points:
        lines.append("\n## Entry points")
        for p in d.entry_points:
            lines.append(f"- `{p}`")

    # Tree
    if d.tree_lines:
        lines.append("\n## Structure (depth-limited)")
        lines.append("```")
        lines.extend(d.tree_lines)
        lines.append("```")

    # Intent / decisions
    if d.readme_excerpt:
        lines.append("\n## Intent (README excerpt)")
        lines.append(d.readme_excerpt)
    if d.doc_titles:
        lines.append("\n## Docs present")
        lines.append(", ".join(f"`{t}`" for t in d.doc_titles[:30]))
    if d.commit_subjects:
        lines.append("\n## Recent changes (commit subjects)")
        for s in d.commit_subjects[:20]:
            lines.append(f"- {s}")

    return "\n".join(lines)


def _file_block(f: DigestFile, level: int) -> str:
    top = [s for s in f.symbols if not s.parent]
    by_parent: dict[str, list] = {}
    for s in f.symbols:
        if s.parent:
            by_parent.setdefault(s.parent, []).append(s)

    if not f.symbols:
        return f"#### `{f.path}`  ({f.lang}, {f.line_count} ln) — no extracted symbols"

    out = [f"#### `{f.path}`  ({f.lang}, {f.line_count} ln)"]
    for s in sorted(top, key=lambda x: x.start_line):
        doc = f" — {s.docstring}" if s.docstring else ""
        out.append(f"- `{s.signature}`  ·L{s.start_line}{doc}")
        if level >= 2 and s.name in by_parent:
            for m in sorted(by_parent[s.name], key=lambda x: x.start_line):
                mdoc = f" — {m.docstring}" if m.docstring else ""
                out.append(f"    - `{m.signature}`  ·L{m.start_line}{mdoc}")
    return "\n".join(out)


def render_digest(
    d: Digest, *, level: int = 1, budget_tokens: int = 120_000
) -> tuple[str, dict[str, Any]]:
    brief = _brief(d, level, budget_tokens)
    used = estimate_tokens(brief)
    parts = [brief]
    shown = 0
    truncated = False

    if level >= 1 and d.files:
        parts.append("\n## File outlines")
        used += estimate_tokens("\n## File outlines")
        # files already sorted by path from list_files()
        ordered = d.files
        for f in ordered:
            block = _file_block(f, level)
            cost = estimate_tokens(block) + 2
            if used + cost > budget_tokens - _RESERVE_TOKENS:
                truncated = True
                break
            parts.append(block)
            used += cost
            shown += 1

        if truncated:
            remaining = d.total_files - shown
            sample = [f.path for f in ordered[shown: shown + 30]]
            ptr = [
                "\n---",
                f"> **Outline truncated to fit budget.** Showed {shown}/{d.total_files} files "
                f"({remaining} more). Nothing is lost — drill in with:",
                "> - `reflens_map(repo, path_glob=\"<dir>/**\", level=2)` to expand a subtree",
                "> - `reflens_search(repo, \"<query>\")` to jump to relevant code",
                "> - `reflens_read(repo, \"<path>\")` for byte-exact source",
                "",
                "> Next files not shown: " + ", ".join(f"`{p}`" for p in sample),
            ]
            block = "\n".join(ptr)
            parts.append(block)
            used += estimate_tokens(block)

    text = "\n".join(parts)
    stats = {
        "tokens_est": used,
        "token_accurate": is_accurate(),
        "budget": budget_tokens,
        "files_shown": shown if level >= 1 else 0,
        "files_total": d.total_files,
        "truncated": truncated,
        "level": level,
    }
    return text, stats
