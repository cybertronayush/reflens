"""Symbol-level semantic index: faster to build + concept-accurate, lossless intact."""

from __future__ import annotations

import pytest

from reflens.engine import Repo
from reflens.ingest import ingest_source
from reflens.search.semantic import compose_symbol_text


def test_compose_symbol_text():
    assert compose_symbol_text("function", "run", "def run(self) -> int", "Runs it.") == (
        "function run def run(self) -> int: Runs it."
    )
    assert compose_symbol_text("class", "Foo", "class Foo", None) == "class Foo class Foo"


def test_semantic_embeds_symbols_and_retrieves(sample_repo):
    pytest.importorskip("fastembed")
    pytest.importorskip("numpy")
    ingest_source("sem", str(sample_repo), semantic=True)
    with Repo.open("sem") as r:
        assert r.db.has_embeddings()
        units = {row["unit"] for row in r.db.iter_embeddings()}
        assert units == {"symbol"}  # semantic index is over the symbol surface
        hits = r.search("the engine that runs things", k=5, mode="semantic")
        assert hits
        assert all(h.kind == "symbol" for h in hits)
        # the Engine class (docstring "Runs things.") should be retrievable
        assert any(h.name == "Engine" for h in hits)
        # losslessness is unaffected by the semantic-unit change
        assert r.verify()["ok"] is True


def test_lossless_unaffected_without_semantic(sample_repo):
    ingest_source("nosem", str(sample_repo))  # lexical only
    with Repo.open("nosem") as r:
        assert r.db.has_embeddings() is False
        assert r.verify()["ok"] is True
        assert r.search("helper", k=3)  # lexical still works
