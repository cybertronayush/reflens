"""Hybrid retrieval: fuse lexical (FTS5/bm25), symbol-name, and semantic results.

Fusion is Reciprocal Rank Fusion (RRF): score = sum 1/(K + rank_i) over each
ranked list a document appears in. RRF is scale-free — it needs no normalization
between bm25 distances and cosine similarities, which is exactly why it's robust
when combining heterogeneous rankers.
"""

from __future__ import annotations

from typing import Optional

from ..models import Hit
from ..store import Database

_RRF_K = 60
_SNIPPET_LINES = 16
_SNIPPET_CHARS = 1000

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

    out: list[Hit] = []
    for key, sc in scores.items():
        h = best[key]
        h.score = round(sc, 6)
        srcs = sources[key]
        h.source = "hybrid" if len(srcs) > 1 else next(iter(srcs))
        out.append(h)
    out.sort(key=lambda h: h.score, reverse=True)
    return out[:k]
