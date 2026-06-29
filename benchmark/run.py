#!/usr/bin/env python3
"""reflens retrieval benchmark — reflens vs. the native-grep baseline.

Honest scope: this measures the RETRIEVAL layer (does the approach surface the
right code, and at what token cost), not full agent task success (which needs a
live agent loop). It is fully reproducible: it runs the same natural-language
queries through reflens's hybrid search and through the baseline an agent
actually uses — ripgrep the query's content words over the cloned source.

Metrics per task:
  - found:  did the approach surface the known target?
            reflens -> target in top-k search hits (path or symbol name)
            baseline -> target file in the union of ripgrep matches
  - rank:   (reflens) 1-indexed rank of the first correct hit; MRR aggregates it
  - tokens: tokens the agent must read to get the answer in context
            reflens  -> snippets of hits 1..rank  (chars/4)
            baseline -> bytes of ALL matched files /4 (cost to be SURE it's there)

Run:  python benchmark/run.py --repo hr            # repo must be `reflens add`-ed
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# (natural-language query, substring that identifies the correct target).
# Queries are phrased in plain English so the words deliberately differ from the
# code's identifiers — the realistic "I don't know the symbol name yet" case.
TASKS = [
    ("how is the number of tokens in a piece of text counted", "tokeniz"),
    ("detect what kind of content a string is and pick the right compressor", "content_router"),
    ("store the original content so compression can be reversed and retrieved later", "ccr"),
    ("compress a large JSON array of similar objects", "smart_crusher"),
    ("parse server sent events from a streaming http response", "streaming"),
    ("wrap a coding agent from the command line", "wrap"),
    ("keep a vector store of memories with embeddings", "memory"),
    ("stabilize the prompt prefix so the provider cache hits", "cache"),
    ("decide compression aggressiveness from observed tool-output patterns", "toin"),
    ("download and install the rtk binary from github releases", "rtk"),
    ("strip comments and shrink code while keeping signatures", "code_compressor"),
    ("track how many tokens were saved and the cost", "savings"),
]

_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "is", "are", "be", "from", "for",
    "in", "on", "how", "what", "so", "it", "that", "with", "while", "keep", "can",
    "right", "kind", "piece", "number", "many", "were", "was", "this",
}


def _toks(text: str) -> int:
    return max(1, len(text) // 4)


def content_words(q: str) -> list[str]:
    words = re.findall(r"[a-zA-Z_]{3,}", q.lower())
    return [w for w in dict.fromkeys(words) if w not in _STOP]


def run_reflens(repo, query: str, target: str, k: int = 8):
    hits = repo.search(query, k=k, mode="auto")
    cum = 0
    for rank, h in enumerate(hits, 1):
        cum += _toks(h.snippet or "")
        hay = f"{h.path} {h.name or ''}".lower()
        if target.lower() in hay:
            return True, rank, cum
    total = sum(_toks(h.snippet or "") for h in hits)
    return False, 0, total


def run_baseline(srcdir: Path, query: str, target: str):
    terms = content_words(query)
    if not terms:
        return False, 0, 0
    cmd = ["rg", "-l", "-i", "-g", "!**/node_modules/**"]
    for t in terms:
        cmd += ["-e", t]
    cmd.append(str(srcdir))
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return False, 0, 0
    files = [f for f in out.splitlines() if f.strip()]
    found = any(target.lower() in f.lower() for f in files)
    tokens = 0
    for f in files:
        try:
            tokens += _toks(os.path.getsize(f) and Path(f).read_bytes().decode("utf-8", "replace"))
        except OSError:
            continue
    return found, len(files), tokens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="hr")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    from reflens.engine import Repo

    repo = Repo.open(args.repo)
    src = Path((repo.meta() or {}).get("source_ref", ""))
    rows = []
    rl_found = rl_tok = rr = 0
    bl_found = bl_tok = 0
    for q, target in TASKS:
        rf, rank, rtok = run_reflens(repo, q, target)
        bf, nfiles, btok = run_baseline(src, q, target)
        rl_found += int(rf)
        rl_tok += rtok
        rr += (1.0 / rank if rf else 0.0)
        bl_found += int(bf)
        bl_tok += btok
        rows.append((q, target, rf, rank, rtok, bf, nfiles, btok))
    repo.close()

    n = len(TASKS)
    lines = []
    lines.append(f"# reflens retrieval benchmark — `{args.repo}`\n")
    lines.append(f"Source: `{src}`  ·  {n} natural-language retrieval tasks\n")
    lines.append("| # | query | target | reflens found@8 (rank, tok) | grep found (files, tok) |")
    lines.append("|--:|---|---|---|---|")
    for i, (q, tgt, rf, rank, rtok, bf, nf, btok) in enumerate(rows, 1):
        rfx = f"✅ #{rank}, {rtok}t" if rf else "❌"
        bfx = f"✅ {nf}f, {btok}t" if bf else f"❌ {nf}f"
        lines.append(f"| {i} | {q} | `{tgt}` | {rfx} | {bfx} |")
    lines.append("")
    lines.append("## Aggregate\n")
    lines.append("| metric | reflens | native grep |")
    lines.append("|---|--:|--:|")
    lines.append(f"| hit-rate @8 | **{rl_found}/{n} ({100*rl_found//n}%)** | {bl_found}/{n} ({100*bl_found//n}%) |")
    lines.append(f"| MRR | **{rr/n:.2f}** | — |")
    lines.append(f"| mean tokens to answer | **{rl_tok//n}** | {bl_tok//n} |")
    if bl_tok and rl_tok:
        lines.append(f"| token efficiency | **{bl_tok/rl_tok:.0f}× fewer** | 1× |")
    report = "\n".join(lines)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n")
        print(f"\nwrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
