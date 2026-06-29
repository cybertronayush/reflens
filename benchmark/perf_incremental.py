#!/usr/bin/env python3
"""Measure incremental vs full semantic re-ingest on an indexed repo.

Baseline = full re-embed (reuse_embeddings=False, the pre-0.3 behavior).
Incremental = unchanged source, so every symbol surface is reused.
The embedder is warmed first so we time embedding COMPUTE, not model load.

Usage:  python benchmark/perf_incremental.py <repo_name> <source_path>
"""

from __future__ import annotations

import sys
import time

from reflens.ingest import ingest_source
from reflens.search.semantic import get_embedder


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "hr"
    src = sys.argv[2] if len(sys.argv) > 2 else "/Users/ayushsingh/Desktop/headroom"

    if get_embedder(None) is None:
        print("semantic extra not installed; nothing to measure")
        return 1

    t = time.perf_counter()
    a = ingest_source(name, src, semantic=True, reuse_embeddings=False)
    ta = time.perf_counter() - t
    print(f"[FULL re-embed]  {ta:7.1f}s  embedded={a.embedded_symbols} reused={a.reused_embeddings}", flush=True)

    t = time.perf_counter()
    b = ingest_source(name, src, semantic=True, reuse_embeddings=True)
    tb = time.perf_counter() - t
    print(f"[INCREMENTAL  ]  {tb:7.1f}s  embedded={b.embedded_symbols} reused={b.reused_embeddings}", flush=True)

    if tb > 0:
        print(f"[SPEEDUP      ]  {ta / tb:.1f}x faster on an unchanged re-ingest", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
