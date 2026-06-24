"""Tests for the gap-closing features: architecture, navigation, conventions,
coverage, history, install guidance, enrichment credentials."""

from __future__ import annotations

import pytest

from reflens.cli import install as install_mod
from reflens.engine import Repo
from reflens.ingest import ingest_source


def test_modules_and_internal_centrality(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        mods = {m["name"]: m for m in r.modules()}
        assert "pkg" in mods
        assert mods["pkg"]["files"] == 2
        # pkg/core.py imports .util -> util has an internal dependent
        assert mods["pkg"]["dependents"] >= 1


def test_architecture_in_digest(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        text, _ = r.map(level=0)
    assert "Architecture (modules" in text
    assert "pkg" in text
    assert "Most depended-on internal files" in text


def test_conventions_detected(tmp_path):
    root = tmp_path / "src"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text("import pytest\ndef test_x():\n    assert 1\n", encoding="utf-8")
    (root / "errors.py").write_text("class MyError(Exception):\n    pass\n", encoding="utf-8")
    (root / "svc.py").write_text("async def go() -> int:\n    return 1\n", encoding="utf-8")
    ingest_source("conv", str(root))
    with Repo.open("conv") as r:
        text, _ = r.map(level=0)
    assert "Conventions detected" in text
    assert "pytest" in text
    assert "custom exception" in text


def test_verify_reports_extraction_coverage(sample_repo):
    ingest_source("demo", str(sample_repo))
    with Repo.open("demo") as r:
        v = r.verify()
    assert "extraction" in v
    assert v["extraction"]["code_files"] >= 2
    assert v["extraction"]["coverage_pct"] >= 50.0


def test_history_unavailable_for_repomix(tmp_path):
    dump = (
        "Repomix packed representation.\n\n## File: a.py\n````python\ndef f():\n    return 1\n````\n"
    )
    p = tmp_path / "d.md"
    p.write_text(dump, encoding="utf-8")
    ingest_source("dmp", str(p))
    with Repo.open("dmp") as r:
        h = r.history()
    assert h["available"] is False


def test_install_guidance_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    msgs1 = install_mod.write_agent_guidance()
    assert msgs1
    target = tmp_path / ".config" / "opencode" / "AGENTS.md"
    assert target.exists()
    body = target.read_text()
    assert install_mod._SNIPPET_START in body
    # second run refreshes, does not duplicate
    install_mod.write_agent_guidance()
    assert target.read_text().count(install_mod._SNIPPET_START) == 1
    # removal
    install_mod.remove_agent_guidance()
    assert install_mod._SNIPPET_START not in target.read_text()


def test_enrich_requires_key(monkeypatch):
    from reflens import enrich

    monkeypatch.delenv("REFLENS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(enrich.EnrichmentError):
        enrich.resolve_credentials(None, None, None)
    key, url, model = enrich.resolve_credentials("sk-test", None, None)
    assert key == "sk-test"
    assert url.endswith("/v1")
    assert model