from __future__ import annotations

from reflens.engine import Repo
from reflens.ingest import ingest_source


def test_finds_symbol(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        hits = r.search("helper", k=5)
    assert hits
    assert any("util.py" in h.path for h in hits)


def test_fts_operator_injection_safe(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        # FTS5 operators must not raise a syntax error.
        hits = r.search('helper* OR (foo: -bar) NEAR "', k=5)
    assert isinstance(hits, list)


def test_empty_query(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        assert r.search("   ", k=5) == []
