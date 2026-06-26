"""Re-ingest safety (atomic swap) + semantic vector cache."""

from __future__ import annotations

from reflens import paths
from reflens.engine import Repo
from reflens.ingest import ingest_source


def test_reingest_is_atomic_and_clean(sample_repo):
    ingest_source("demo", str(sample_repo))
    # update the source, then re-ingest the same name
    (sample_repo / "pkg" / "new_mod.py").write_text(
        "def freshly_added():\n    return 42\n", encoding="utf-8"
    )
    ingest_source("demo", str(sample_repo))

    with Repo.open("demo") as r:
        assert r.verify()["ok"] is True
        hits = r.search("freshly_added", k=5)
        assert any("new_mod.py" in h.path for h in hits)

    # no leftover temp / backup dirs from the swap
    base = paths.repos_dir()
    leftovers = list(base.glob(".reflens-tmp-*")) + list(base.glob(".reflens-old-*"))
    assert leftovers == [], f"swap left junk behind: {leftovers}"


def test_vector_cache_hit(sample_repo):
    import pytest

    np = pytest.importorskip("numpy")
    from reflens.search import hybrid

    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        sym_ids = [row["id"] for row in r.db.conn.execute("SELECT id FROM symbols LIMIT 2")]
        assert len(sym_ids) == 2
        for i, sid in enumerate(sym_ids):
            vec = np.zeros(3, dtype="float32")
            vec[i] = 1.0
            r.db.set_embedding("symbol", sid, 3, vec.tobytes())
        r.db.commit()

        hybrid._VEC_CACHE.clear()
        units1, ids1, m1 = hybrid._load_vectors(r.db, np)
        assert sorted(ids1) == sorted(sym_ids)
        assert m1.shape == (2, 3)
        assert all(u == "symbol" for u in units1)
        # second call returns the SAME cached object (no re-read)
        units2, ids2, m2 = hybrid._load_vectors(r.db, np)
        assert m2 is m1 and ids2 is ids1
