"""Hybrid retrieval: fuse lexical (FTS5/bm25), symbol-name, and semantic results.

Fusion is Reciprocal Rank Fusion (RRF): score = sum 1/(K + rank_i) over each
ranked list a document appears in. RRF is scale-free — it needs no normalization
between bm25 distances and cosine similarities, which is exactly why it's robust
when combining heterogeneous rankers.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import Hit
from ..store import Database

_RRF_K = 60
_SNIPPET_LINES = 16
_SNIPPET_CHARS = 1000

# Test/spec files describe behavior in plain English (their names and docstrings
# read like the query), so they out-compete the implementation on "where is X"
# queries. We demote — never drop — them: recall is preserved (a test still
# appears), but an implementation that ties on relevance ranks above it. Skipped
# entirely when the query is itself about tests. This mirrors how Sourcegraph,
# GitHub code search, and ctags-based tools treat tests for definition lookups.
_TEST_PENALTY = 0.4
_TEST_DIR_SEGMENTS = {"test", "tests", "spec", "specs", "__tests__", "testing"}
_TEST_NAME_PREFIXES = ("test_", "test-")
_TEST_NAME_INFIXES = ("_test.", "-test.", ".test.", ".spec.", "_spec.", "-spec.")


def _is_test_path(path: str) -> bool:
    p = path.lower()
    segments = p.split("/")
    if any(seg in _TEST_DIR_SEGMENTS for seg in segments[:-1]):
        return True
    base = segments[-1]
    if base.startswith(_TEST_NAME_PREFIXES):
        return True
    return any(infix in base for infix in _TEST_NAME_INFIXES)


def _query_wants_tests(query: str) -> bool:
    toks = query.lower().replace("-", " ").replace("_", " ").split()
    return any(t in ("test", "tests", "spec", "specs", "testing") for t in toks)


# Dependency-aware result selection (opt-in). RRF scores each hit independently,
# so a top-k list can be dominated by near-duplicates (e.g. several tests of the
# same function) — the retrieval analog of DSpark's "multi-modal collision". MMR
# conditions result k on results 1..k-1 (the semi-autoregressive head), and the
# marginal-value early-stop is the greedy prefix scheduler: keep admitting while
# a candidate adds net value, stop when redundancy outweighs relevance.
_MMR_LAMBDA = 0.5  # relevance weight; (1-λ) weights the redundancy penalty.
_REL_FLOOR = 0.1  # stop once the best candidate's relevance falls below this.
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _redundancy_tokens(h: Hit) -> set:
    """Bag of content words for a hit — its symbol name plus the snippet head.
    Two hits that describe the same code share most of these."""
    text = f"{h.name or ''} {(h.snippet or '')[:240]}"
    return {w.lower() for w in _WORD_RE.findall(text) if len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _mmr_select(ranked: list[Hit], k: int, lambda_: float = _MMR_LAMBDA) -> list[Hit]:
    """Re-rank a relevance-sorted list by Maximal Marginal Relevance and cut the
    redundant/low-value tail. Always keeps the top (highest-relevance) hit and
    returns between 1 and k hits. Relevance is each hit's fused score normalized
    to the top hit; redundancy is Jaccard overlap of content words with the
    already-selected hits.
    """
    if len(ranked) <= 1:
        return ranked[:k]
    top = ranked[0].score or 0.0
    norm = top if top > 0 else 1.0
    toks = {id(h): _redundancy_tokens(h) for h in ranked}
    selected = [ranked[0]]
    pool = ranked[1:]
    while pool and len(selected) < k:
        best_i, best_val, best_rel = -1, 0.0, 0.0
        for i, h in enumerate(pool):
            rel = (h.score or 0.0) / norm
            red = max(_jaccard(toks[id(h)], toks[id(s)]) for s in selected)
            val = lambda_ * rel - (1.0 - lambda_) * red
            if best_i == -1 or val > best_val:
                best_i, best_val, best_rel = i, val, rel
        if best_val <= 0.0 or best_rel < _REL_FLOOR:
            break  # remaining candidates are redundant or irrelevant -> stop
        selected.append(pool.pop(best_i))
    return selected

# In-process cache of (signature, units, ids, matrix) per DB file. Without it,
# semantic search re-reads and re-stacks every vector from SQLite on every query —
# the dominant per-query cost. The signature changes when the index is re-ingested
# (atomic swap replaces the file), auto-invalidating the cache.
_VEC_CACHE: dict = {}


def _load_vectors(db: Database, np):
    """Return (units, ids, matrix) of all embeddings, cached per DB file.

    `units[i]` is the unit kind ('symbol' by default) for `ids[i]`.
    """
    path, sig = db.file_signature()
    if path is not None:
        cached = _VEC_CACHE.get(path)
        if cached is not None and cached[0] == sig:
            return cached[1], cached[2], cached[3]
    units: list[str] = []
    ids: list[int] = []
    mat: list = []
    for row in db.iter_embeddings():
        units.append(row["unit"])
        ids.append(int(row["unit_id"]))
        mat.append(np.frombuffer(row["vec"], dtype="float32"))
    matrix = np.vstack(mat) if mat else None
    if path is not None:
        _VEC_CACHE[path] = (sig, units, ids, matrix)
    return units, ids, matrix


def _snippet(text: str) -> str:
    lines = text.split("\n")
    head = "\n".join(lines[:_SNIPPET_LINES])
    if len(head) > _SNIPPET_CHARS:
        head = head[:_SNIPPET_CHARS].rstrip() + "\u2026"
    return head


def _semantic_symbol_hits(
    db: Database, query: str, limit: int, model: Optional[str]
) -> list[Hit]:
    """Rank symbols by cosine over the symbol-surface embeddings -> Hit list."""
    if not db.has_embeddings():
        return []
    try:
        import numpy as np  # type: ignore
    except Exception:
        return []
    from .semantic import get_embedder

    embedder = get_embedder(model)
    if embedder is None:
        return []
    units, ids, matrix = _load_vectors(db, np)
    if matrix is None or not ids:
        return []
    qvec = np.frombuffer(embedder.embed_query(query), dtype="float32")
    if matrix.shape[1] != qvec.shape[0]:
        # Index built with a different-dimension embedding model than the one
        # loaded now — degrade to lexical instead of raising on the matmul.
        return []
    sims = matrix @ qvec  # vectors are L2-normalized => dot == cosine
    top = np.argsort(-sims)[:limit]
    sym_ids = [ids[i] for i in top if units[i] == "symbol"]
    rows = db.symbols_by_ids(sym_ids)
    hits: list[Hit] = []
    for sid in sym_ids:
        r = rows.get(sid)
        if r is None:
            continue
        hits.append(
            Hit(path=r["path"], start_line=r["start_line"], end_line=r["end_line"],
                score=0.0, snippet=f"{r['kind']} {r['name']}: {r['signature']}",
                kind="symbol", name=r["name"], source="semantic")
        )
    return hits


def search(
    db: Database,
    query: str,
    *,
    k: int = 10,
    mode: str = "auto",
    embed_model: Optional[str] = None,
    diversify: bool = False,
) -> list[Hit]:
    query = (query or "").strip()
    if not query:
        return []
    pool = max(k * 3, 20)
    use_lexical = mode in ("auto", "lexical", "hybrid")
    use_semantic = mode in ("auto", "semantic", "hybrid")

    # Each ranker yields an ordered list of Hits; we fuse heterogeneous result
    # types (lexical chunks, semantic symbols, symbol-name matches) by a common
    # (path, line, kind) key with Reciprocal Rank Fusion.
    ranked_lists: list[list[Hit]] = []

    if use_lexical:
        ranked_lists.append([
            Hit(path=r["path"], start_line=r["start_line"], end_line=r["end_line"],
                score=0.0, snippet=_snippet(r["text"]), kind="chunk", source="lexical")
            for r in db.search_chunks_fts(query, limit=pool)
        ])
        ranked_lists.append([
            Hit(path=r["path"], start_line=r["start_line"], end_line=r["end_line"],
                score=0.0, snippet=f"{r['kind']} {r['name']}: {r['signature']}",
                kind="symbol", name=r["name"], source="lexical")
            for r in db.search_symbols_fts(query, limit=pool)
        ])

    if use_semantic:
        sem = _semantic_symbol_hits(db, query, pool, embed_model)
        if sem:
            ranked_lists.append(sem)

    scores: dict[tuple, float] = {}
    best: dict[tuple, Hit] = {}
    sources: dict[tuple, set] = {}
    for lst in ranked_lists:
        for rank, h in enumerate(lst):
            key = (h.path, h.start_line, h.kind)
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
            best.setdefault(key, h)
            sources.setdefault(key, set()).add(h.source)

    penalize_tests = not _query_wants_tests(query)
    out: list[Hit] = []
    for key, sc in scores.items():
        h = best[key]
        if penalize_tests and _is_test_path(h.path):
            sc *= _TEST_PENALTY
        h.score = round(sc, 6)
        srcs = sources[key]
        h.source = "hybrid" if len(srcs) > 1 else next(iter(srcs))
        out.append(h)
    out.sort(key=lambda h: h.score, reverse=True)
    if diversify:
        return _mmr_select(out, k)
    return out[:k]
