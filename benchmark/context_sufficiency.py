#!/usr/bin/env python3
"""Context-sufficiency eval — does the ANSWER reach the agent's context window?

The retrieval benchmark answers "does the right file rank?". This answers the
question the premise actually rests on: at a FIXED token budget (the slice of
context an agent gives to repo knowledge), does the answer-bearing file get READ
into context at all? All three methods read file content into the budget — the
only variable is which files, in what order (the real agent loop is "find files,
then read them"):

  - reflens   : files ordered by diversified hybrid-search relevance (best first).
  - grep-dump : files matching the query's content words, in ripgrep's order.
  - full-dump : files in path order — "paste the repo top-down".

Ground truth is honest: for each task we locate the canonical (non-test) source
file that answers it. "Grounded" = that exact file was read within the budget.
No symbol heuristics, no string-matching — just file inclusion, tracked directly.

Honest limitation: reading the answer file is necessary for the agent to answer,
not sufficient (it still has to use the content). This measures reachability of
the answer in-window, not final-answer correctness. It is also author-built — use
strong realistic baselines (grep, paste-the-repo) and read the losses too.

Run:  python benchmark/context_sufficiency.py --repo hr
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import TASKS, content_words  # noqa: E402  (reuse the same tasks)

BUDGETS = [1500, 3000, 6000, 12000]


def _is_test(path: str) -> bool:
    p = path.lower()
    return "test" in p or "/spec" in p or p.endswith("conftest.py")


def find_source_file(src: Path, target: str) -> Path | None:
    """Canonical non-test source file whose path matches the target substring."""
    cands = [
        p for p in src.rglob("*.py")
        if target.lower() in str(p).lower()
        and not _is_test(str(p))
        and "node_modules" not in str(p)
    ]
    if not cands:
        return None
    cands.sort(key=lambda p: (target.lower() not in p.name.lower(), len(str(p))))
    return cands[0].resolve()


def _files_within_budget(src: Path, rel_paths, budget: int) -> set:
    """Resolved abspaths of the files (fully or partially) read before the budget
    is exhausted, reading them in the given order."""
    included, total = set(), 0
    for rel in rel_paths:
        if total >= budget:
            break
        p = (src / rel) if not os.path.isabs(str(rel)) else Path(rel)
        try:
            nchars = len(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        included.add(str(p.resolve()))
        read_chars = min(nchars, (budget - total) * 4)
        total += max(1, read_chars // 4)
    return included


def reflens_files(repo, src: Path, query: str, budget: int) -> set:
    """reflens reads the symbol's line-EXTENT per hit (a ranged read — its actual
    capability), not whole files. grep/dump have no structure and must read whole
    files. Modeling each tool's real behavior is the point of the comparison."""
    included, total = set(), 0
    for h in repo.search(query, k=25, mode="auto", diversify=True):
        if total >= budget:
            break
        p = src / h.path
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        start = max(0, (h.start_line or 1) - 1)
        end = h.end_line or (start + 1)
        seg = "\n".join(lines[start:end])
        included.add(str(p.resolve()))
        total += max(1, len(seg) // 4)
    return included


def grep_files(src: Path, query: str, budget: int) -> set:
    terms = content_words(query)
    if not terms:
        return set()
    cmd = ["rg", "-l", "-i", "-g", "!**/node_modules/**"]
    for t in terms:
        cmd += ["-e", t]
    cmd.append(str(src))
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return set()
    return _files_within_budget(src, [f for f in out.splitlines() if f.strip()], budget)


def dump_files(src: Path, budget: int) -> set:
    files = [str(p) for p in sorted(src.rglob("*.py")) if "node_modules" not in str(p)]
    return _files_within_budget(src, files, budget)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="hr")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    from reflens.engine import Repo

    repo = Repo.open(args.repo)
    src = Path((repo.meta() or {}).get("source_ref", ""))

    graded = []  # (question, answer_file_abspath)
    for q, target in TASKS:
        f = find_source_file(src, target)
        if f is not None:
            graded.append((q, str(f)))

    # --- Correct-usage check: reflens is navigation-first (map -> drill -> read),
    # not search-only. Measure whether the DESIGNED flow surfaces the answer file
    # (names it in the architecture digest or a module drill-down) vs search alone.
    l0 = repo.map(level=0)[0]
    drill_cache: dict[str, str] = {}
    search_only = navigation = 0
    for q, answer in graded:
        base, module = Path(answer).name, Path(answer).parent.name
        rel = str(Path(answer).resolve()).replace(str(src.resolve()) + "/", "")
        if module not in drill_cache:
            drill_cache[module] = repo.map(path_glob=f"**/{module}/**", level=1)[0]
        surfaced_nav = base in l0 or rel in l0 or base in drill_cache[module]
        surfaced_search = any(
            str((src / h.path).resolve()) == answer
            for h in repo.search(q, k=25, mode="auto")
        )
        navigation += surfaced_nav or surfaced_search
        search_only += surfaced_search

    res = {b: {"reflens": 0, "grep-dump": 0, "full-dump": 0} for b in BUDGETS}
    for b in BUDGETS:
        dump = dump_files(src, b)  # query-independent
        for q, answer in graded:
            if answer in reflens_files(repo, src, q, b):
                res[b]["reflens"] += 1
            if answer in grep_files(src, q, b):
                res[b]["grep-dump"] += 1
            if answer in dump:
                res[b]["full-dump"] += 1
    repo.close()

    n = len(graded)
    lines = [
        f"# Context-sufficiency eval — `{args.repo}`\n",
        f"Source: `{src}`  ·  {n} graded tasks\n",
        "## Correct usage matters: navigation-first vs search-only\n",
        "reflens is designed to be used map -> drill -> read, not search-in-isolation.",
        "Does the flow surface the answer file at all?\n",
        f"- **reflens, used as designed (map + module drill-down): {navigation}/{n} "
        f"({100 * navigation // n if n else 0}%)**",
        f"- reflens, search() in isolation: {search_only}/{n} "
        f"({100 * search_only // n if n else 0}%)  ← what a naive integration (and my earlier "
        "benchmarks) measured\n",
        "## Budget check (search-only path): was the answer file READ within budget?\n",
        "| token budget | reflens (search) | grep-dump | full-dump |", "|--:|--:|--:|--:|",
    ]
    for b in BUDGETS:
        r = res[b]
        cell = lambda m: f"{r[m]}/{n} ({100 * r[m] // n if n else 0}%)"  # noqa: E731
        lines.append(f"| {b} | **{cell('reflens')}** | {cell('grep-dump')} | {cell('full-dump')} |")
    report = "\n".join(lines)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n")
        print(f"\nwrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
