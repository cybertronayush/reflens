"""Hybrid retrieval: fuse lexical (FTS5/bm25), symbol-name, and semantic results.

Fusion is Reciprocal Rank Fusion (RRF): score = sum 1/(K + rank_i) over each
ranked list a document appears in. RRF is scale-free — it needs no normalization
between bm25 distances and cosine similarities, which is exactly why it's robust
when combining heterogeneous rankers.
"""

from __future__ import annotations

import os
from typing import Optional

from ..models import Hit
from ..store import Database

_RRF_K = 60
_SNIPPET_LINES = 16
_SNIPPET_CHARS = 1000

# In-process cache of (signature, ids, matrix) per DB file. Without it, semantic
# search re-reads and re-stacks every vector from SQLite on every query — the
# dominant per-query cost on a large repo. The signature (db file mtime+size plus
# the WAL size) changes when the index is re-ingested, auto-invalidating the cache.
_VEC_CACHE: dict = {}


def _db_signature(db: Database):
    try:
        path = None
        for row in db.conn.execute("PRAGMA database_list"):
            if row[1] == "main" and row[2]:
                path = row[2]
                break
        if not path:
            return None, None
        st = os.stat(path)
        wal = path + "-wal"
        wal_sz = os.path.getsize(wal) if os.path.exists(wal) else 0
        return path, (st.st_mtime_ns, st.st_size, wal_sz)
    except OSError:
        return None, None


def _load_vectors(db: Database, np):
    """Return (ids, matrix) of all chunk embeddings, cached per DB file."""
    path, sig = _db_signature(db)
    if path is not None:
        cached = _VEC_CACHE.get(path)
        if cached is not None and cached[0] == sig:
            return cached[1], cached[2]
    ids: list[int] = []
    mat: list = []
    for row in db.iter_embeddings():
        ids.append(int(row["chunk_id"]))
        mat.append(np.frombuffer(row["vec"], dtype="float32"))
    matrix = np.vstack(mat) if mat else None
    if path is not None:
        _VEC_CACHE[path] = (sig, ids, matrix)
    return ids, matrix


def _snippet(text: str) -> str:
    lines = text.split("\n")
    head = "\n".join(lines[:_SNIPPET_LINES])
    if len(head) > _SNIPPET_CHARS:
        head = head[:_SNIPPET_CHARS].rstrip() + "\u2026"
    return head


def _rrf_add(scores: dict, ranked_ids: list, weight: float = 1.0) -> None:
    for rank, key in enumerate(ranked_ids):
        scores[key] = scores.get(key, 0.0) + weight / (_RRF_K + rank + 1)


def _semantic_ranked_chunk_ids(
    db: Database, query: str, limit: int, model: Optional[str]
) -> list[int]:
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
    qvec = np.frombuffer(embedder.embed_query(query), dtype="float32")

    ids, matrix = _load_vectors(db, np)
    if not ids or matrix is None:
        return []
    sims = matrix @ qvec  # vectors are L2-normalized => dot == cosine
    top = np.argsort(-sims)[:limit]
    return [ids[i] for i in top]


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
    chunk_scores: dict[int, float] = {}
    chunk_rows: dict[int, dict] = {}

    use_lexical = mode in ("auto", "lexical", "hybrid")
    use_semantic = mode in ("auto", "semantic", "hybrid")

    if use_lexical:
        lex = db.search_chunks_fts(query, limit=pool)
        ids = [int(r["id"]) for r in lex]
        for r in lex:
            chunk_rows[int(r["id"])] = dict(r)
        _rrf_add(chunk_scores, ids, weight=1.0)

    if use_semantic:
        sem_ids = _semantic_ranked_chunk_ids(db, query, pool, embed_model)
        for cid in sem_ids:
            if cid not in chunk_rows:
                row = db.get_chunk(cid)
                if row is None:
                    continue
                f = db.conn.execute(
                    "SELECT path FROM files WHERE id=?", (row["file_id"],)
                ).fetchone()
                chunk_rows[cid] = {
                    "id": cid, "path": f["path"] if f else "?",
                    "start_line": row["start_line"], "end_line": row["end_line"],
                    "text": row["text"],
                }
        _rrf_add(chunk_scores, sem_ids, weight=1.0)

    hits: list[Hit] = []
    for cid, score in chunk_scores.items():
        r = chunk_rows.get(cid)
        if not r:
            continue
        src = "hybrid" if use_lexical and use_semantic and db.has_embeddings() else (
            "semantic" if use_semantic and db.has_embeddings() and not use_lexical else "lexical"
        )
        hits.append(
            Hit(path=r["path"], start_line=r["start_line"], end_line=r["end_line"],
                score=round(score, 6), snippet=_snippet(r["text"]),
                kind="chunk", source=src)
        )

    # High-signal symbol-name matches, folded in (lexical FTS over symbols).
    if use_lexical:
        for r in db.search_symbols_fts(query, limit=k):
            hits.append(
                Hit(path=r["path"], start_line=r["start_line"], end_line=r["end_line"],
                    score=round(1.0 / (_RRF_K + 1), 6) + 0.001,
                    snippet=f"{r['kind']} {r['name']}: {r['signature']}",
                    kind="symbol", name=r["name"], source="lexical")
            )

    hits.sort(key=lambda h: h.score, reverse=True)
    # Dedup by (path, start_line), keep best.
    seen: set = set()
    deduped: list[Hit] = []
    for h in hits:
        key = (h.path, h.start_line, h.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    return deduped[:k]
