"""The crux: ingestion must lose nothing. Proven two ways."""

from __future__ import annotations

from pathlib import Path

from reflens.engine import Repo
from reflens.ingest import ingest_source


def test_verify_passes(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        res = r.verify()
    assert res["ok"] is True
    assert res["failed"] == []
    assert res["files"] == res["verified"] > 0


def test_blob_bytes_identical_to_source(sample_repo: Path):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        for row in r.db.list_files():
            original = (sample_repo / row["path"]).read_bytes()
            stored = r.blobs.get(row["sha256"])
            assert stored == original, f"byte mismatch for {row['path']}"


def test_read_roundtrips_text(sample_repo: Path):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        res = r.read("notes.txt")
        original = (sample_repo / "notes.txt").read_text(encoding="utf-8")
        assert res["content"] == original  # unicode + tabs + fenced content intact


def test_read_symbol_body(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        res = r.read("Engine")
        assert res["kind"] == "symbol"
        assert res["bodies"]
        assert "class Engine" in res["bodies"][0]["content"]
