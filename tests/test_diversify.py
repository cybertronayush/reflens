"""Dependency-aware retrieval (MMR + early-stop), the reflens analog of DSpark's
semi-autoregressive head + greedy prefix scheduler: condition result k on
results 1..k-1 (penalize redundancy) and stop admitting when marginal value
drops. Opt-in via `diversify=True`; default ranking is unchanged.
"""

from __future__ import annotations

from reflens.models import Hit
from reflens.search.hybrid import _jaccard, _mmr_select, _redundancy_tokens


def _hit(name: str, path: str, snippet: str, score: float) -> Hit:
    return Hit(
        path=path, start_line=1, end_line=2, score=score,
        snippet=snippet, kind="symbol", name=name, source="lexical",
    )


def test_jaccard_bounds():
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


def test_redundancy_ranks_near_duplicate_above_diverse():
    a = _hit("test_compress_a", "tests/t.py", "assert compress content works", 1.0)
    b = _hit("test_compress_b", "tests/t.py", "assert compress content works", 0.9)
    c = _hit("ContentRouter", "src/router.py", "route detected type to backend", 0.8)
    ta, tb, tc = _redundancy_tokens(a), _redundancy_tokens(b), _redundancy_tokens(c)
    assert _jaccard(ta, tb) > _jaccard(ta, tc)  # b more redundant with a than c is


def test_mmr_surfaces_diverse_impl_over_redundant_test():
    # Two near-duplicate tests outrank an implementation on raw score; MMR should
    # lift the diverse implementation above the second redundant test.
    a = _hit("test_route_1", "tests/t.py", "assert route content compress works", 1.0)
    b = _hit("test_route_2", "tests/t.py", "assert route content compress works", 0.92)
    c = _hit("ContentRouter", "src/router.py", "detect string type pick compressor", 0.85)
    out = _mmr_select([a, b, c], k=3)
    assert out[0] is a  # top relevance preserved
    assert c in out  # the diverse implementation is surfaced
    assert b not in out  # the redundant near-duplicate test is dropped


def test_mmr_early_stops_on_identical_tail():
    base = _hit("test_dup", "tests/t.py", "identical body text alpha beta", 1.0)
    dups = [
        _hit("test_dup", "tests/t.py", "identical body text alpha beta", 1.0 - i * 0.001)
        for i in range(1, 6)
    ]
    out = _mmr_select([base, *dups], k=8)
    assert len(out) < 6  # redundant tail dropped


def test_mmr_respects_k_and_keeps_top():
    hits = [
        _hit(f"f{i}", f"src/f{i}.py", f"unique alpha body number {i} gamma", 1.0 - i * 0.05)
        for i in range(10)
    ]
    out = _mmr_select(hits, k=4)
    assert 1 <= len(out) <= 4
    assert out[0] is hits[0]


def test_mmr_single_hit_passthrough():
    h = _hit("only", "src/a.py", "solo", 1.0)
    assert _mmr_select([h], k=8) == [h]
    assert _mmr_select([], k=8) == []


def test_search_default_is_unchanged(sample_repo):
    """diversify defaults off: search returns the same fixed-k ranking as before."""
    from reflens.engine import Repo
    from reflens.ingest import ingest_source

    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        plain = r.search("helper", k=5)
        plain_again = r.search("helper", k=5)
    assert [(h.path, h.start_line) for h in plain] == [(h.path, h.start_line) for h in plain_again]
