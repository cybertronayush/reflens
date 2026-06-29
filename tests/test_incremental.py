"""Incremental semantic ingest: reuse a symbol's prior embedding when its
composed embed-text is unchanged, so re-ingesting a mostly-unchanged repo
doesn't re-run the expensive embedding pass over every symbol.

Contract under test (must hold for correctness, not just speed):
  1. no-change re-ingest -> every symbol's vector is BYTE-IDENTICAL to before.
  2. changed symbol re-embeds; unchanged symbols in the same ingest are reused.
  3. a different embedding model busts reuse entirely (vectors aren't portable).
  4. first ingest / no prior index / incompatible prior -> full embed, no crash.
  5. `verify` still proves byte-exact losslessness afterwards.
  6. reuse vs fresh-embed counts are observable on IngestResult.
"""

from __future__ import annotations

import pytest

from reflens.engine import Repo
from reflens.ingest import ingest_source


def _embedder_or_skip():
    from reflens.search.semantic import get_embedder

    emb = get_embedder(None)
    if emb is None:
        pytest.skip("semantic extra (fastembed) not installed")
    return emb


def _vecs(repo) -> dict:
    """{(path, symbol_name, start_line): vec_bytes} — a key stable across ingests."""
    rows = repo.db.conn.execute(
        "SELECT f.path AS path, s.name AS name, s.start_line AS sl, e.vec AS vec "
        "FROM symbols s JOIN files f ON f.id = s.file_id "
        "JOIN embeddings e ON e.unit='symbol' AND e.unit_id = s.id"
    ).fetchall()
    return {(r["path"], r["name"], r["sl"]): r["vec"] for r in rows}


def test_first_ingest_embeds_all_none_reused(sample_repo):
    _embedder_or_skip()
    res = ingest_source("demo", str(sample_repo), semantic=True)
    assert res.embedded_symbols > 0
    assert res.reused_embeddings == 0


def test_reingest_unchanged_reuses_byte_identical_vectors(sample_repo):
    _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)
    with Repo.open("demo") as r:
        before = _vecs(r)
    res = ingest_source("demo", str(sample_repo), semantic=True)
    with Repo.open("demo") as r:
        after = _vecs(r)

    assert before and after
    assert set(before) == set(after)
    for key in before:
        assert before[key] == after[key], f"vector drifted for {key}"
    assert res.reused_embeddings == len(before)
    assert res.embedded_symbols == 0  # nothing changed => nothing re-embedded


def test_changed_symbol_reembeds_unchanged_reused(sample_repo):
    _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)
    with Repo.open("demo") as r:
        before = _vecs(r)

    # Change only util.helper's signature -> its embed-text changes; core.py
    # symbols (Engine/run/main) are untouched so their text is identical.
    (sample_repo / "pkg" / "util.py").write_text(
        "def helper(x: int) -> int:\n    return x * 2\n", encoding="utf-8"
    )
    res = ingest_source("demo", str(sample_repo), semantic=True)
    with Repo.open("demo") as r:
        after = _vecs(r)

    helper_before = next(v for k, v in before.items() if k[1] == "helper")
    helper_after = next(v for k, v in after.items() if k[1] == "helper")
    assert helper_before != helper_after  # re-embedded with the new signature

    main_before = next(v for k, v in before.items() if k[1] == "main")
    main_after = next(v for k, v in after.items() if k[1] == "main")
    assert main_before == main_after  # untouched symbol reused byte-for-byte

    assert res.embedded_symbols >= 1
    assert res.reused_embeddings >= 1


def test_reuse_map_busts_on_fingerprint_mismatch(sample_repo):
    emb = _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)

    from reflens import paths
    from reflens.ingest.base import _load_reuse_map
    from reflens.search.semantic import embed_fingerprint, get_embedder

    prev = paths.repo_dir("demo") / "index.db"
    fp = embed_fingerprint(get_embedder(None))
    same = _load_reuse_map(prev, fp, emb.dim)
    assert len(same) > 0  # identical pipeline => reuse available
    other = _load_reuse_map(prev, "different-model|384|v1", emb.dim)
    assert other == {}  # different fingerprint => no reuse


def test_load_reuse_map_handles_missing_and_dimension_mismatch(sample_repo):
    emb = _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)
    from reflens import paths
    from reflens.ingest.base import _load_reuse_map
    from reflens.search.semantic import embed_fingerprint, get_embedder

    prev = paths.repo_dir("demo") / "index.db"
    fp = embed_fingerprint(get_embedder(None))
    assert _load_reuse_map(prev / "nope.db", fp, emb.dim) == {}  # missing file
    assert _load_reuse_map(prev, fp, emb.dim + 1) == {}  # wrong dimension


def test_corrupt_prior_fingerprint_does_not_crash(sample_repo):
    """A prior fingerprint that is valid JSON but not a string must NOT crash the
    reuse loader (regression: an unguarded .get/parse aborted the whole ingest)."""
    import sqlite3

    emb = _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)
    from reflens import paths
    from reflens.ingest.base import _load_reuse_map
    from reflens.search.semantic import embed_fingerprint, get_embedder

    prev = paths.repo_dir("demo") / "index.db"
    conn = sqlite3.connect(str(prev))
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('embed_fingerprint','42') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    conn.commit()
    conn.close()

    fp = embed_fingerprint(get_embedder(None))
    assert _load_reuse_map(prev, fp, emb.dim) == {}  # degrades, does not raise
    # and a subsequent full re-ingest still completes end to end
    res = ingest_source("demo", str(sample_repo), semantic=True)
    assert res.embedded_symbols + res.reused_embeddings > 0


def test_prior_without_fingerprint_forces_full_embed(sample_repo):
    """A pre-0.3 index (no embed_fingerprint meta) is treated as incompatible."""
    import sqlite3

    emb = _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)
    from reflens import paths
    from reflens.ingest.base import _load_reuse_map
    from reflens.search.semantic import embed_fingerprint, get_embedder

    prev = paths.repo_dir("demo") / "index.db"
    conn = sqlite3.connect(str(prev))
    conn.execute("DELETE FROM meta WHERE key='embed_fingerprint'")
    conn.commit()
    conn.close()

    fp = embed_fingerprint(get_embedder(None))
    assert _load_reuse_map(prev, fp, emb.dim) == {}  # no fingerprint => no reuse


def test_verify_lossless_after_incremental_reingest(sample_repo):
    _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)
    ingest_source("demo", str(sample_repo), semantic=True)
    with Repo.open("demo") as r:
        assert r.verify()["ok"] is True


def test_reuse_can_be_disabled(sample_repo):
    _embedder_or_skip()
    ingest_source("demo", str(sample_repo), semantic=True)
    res = ingest_source("demo", str(sample_repo), semantic=True, reuse_embeddings=False)
    assert res.reused_embeddings == 0
    assert res.embedded_symbols > 0
