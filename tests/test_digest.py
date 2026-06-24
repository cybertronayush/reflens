from __future__ import annotations

from reflens.engine import Repo
from reflens.ingest import ingest_source


def test_digest_contains_surface(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        text, stats = r.map(level=2, budget_tokens=100_000)
    assert "class Engine" in text
    assert "def helper" in text
    assert "Language mix" in text
    assert stats["truncated"] is False


def test_budget_truncates_with_pointer(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        text, stats = r.map(level=2, budget_tokens=350)
    assert stats["truncated"] is True
    assert "Outline truncated" in text
    assert "reflens_read" in text  # the drill-down pointer is present


def test_path_glob_scopes(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        text, stats = r.map(level=2, path_glob="pkg/**")
    assert "pkg/core.py" in text
    assert "README.md" not in text.split("File outlines")[-1]
