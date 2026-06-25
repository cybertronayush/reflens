"""Internal-dependency centrality must not be hijacked by stdlib/common names
on deep files (the architecture brief's "most depended-on" correctness)."""

from __future__ import annotations

from reflens.engine import Repo
from reflens.graph import resolve
from reflens.ingest import ingest_source


def test_stdlib_name_on_deep_file_not_credited(tmp_path):
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "logging.py").write_text("def setup():\n    pass\n", encoding="utf-8")
    # app.py imports the STDLIB logging/os, not pkg/logging
    (root / "app.py").write_text("import logging\nimport os\n", encoding="utf-8")
    ingest_source("c", str(root))
    with Repo.open("c") as r:
        dep = resolve.internal_dependents(r.db)
    assert dep.get("pkg/logging.py", 0) == 0  # stdlib import must not credit it


def test_root_module_bare_import_resolves(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "config.py").write_text("X = 1\n", encoding="utf-8")
    (root / "app.py").write_text("import config\n", encoding="utf-8")
    ingest_source("c2", str(root))
    with Repo.open("c2") as r:
        dep = resolve.internal_dependents(r.db)
    assert dep.get("config.py", 0) >= 1  # bare import of a ROOT module still resolves


def test_qualified_deep_import_resolves(tmp_path):
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "logging.py").write_text("def setup():\n    pass\n", encoding="utf-8")
    (root / "app.py").write_text("import pkg.logging\n", encoding="utf-8")
    ingest_source("c3", str(root))
    with Repo.open("c3") as r:
        dep = resolve.internal_dependents(r.db)
    assert dep.get("pkg/logging.py", 0) >= 1  # qualified ref IS credited
