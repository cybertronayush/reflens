"""Completeness + compressed-dump handling — the gaps found re-verifying repomix."""

from __future__ import annotations

from reflens.engine import Repo
from reflens.extract import extract_outline
from reflens.ingest import ingest_source

# A realistic compressed dump: a.py is body-less with ⋮---- markers (repomix
# --compress), assets/logo.png is in the tree but has no content block (binary).
_DUMP = """This file is a merged representation by Repomix.
Comments removed, content compressed (⋮---- delimiter).

# Directory Structure
```
a.py
assets/logo.png
pkg/
  mod.py
```

## File: a.py
````python
class Foo
⋮----
def bar(self, x: int)
⋮----
````

## File: pkg/mod.py
````python
def baz():
    return 1
````
"""


def _write(tmp_path):
    p = tmp_path / "dump.md"
    p.write_text(_DUMP, encoding="utf-8")
    return p


def test_compressed_python_recovers_symbols():
    # Body-less + ⋮---- => ast.parse fails => regex fallback must still find symbols.
    src = "class Foo\ndef bar(self, x: int)\n"
    o = extract_outline("a.py", src)
    names = {s.name for s in o.symbols}
    assert "Foo" in names
    assert "bar" in names


def test_ingest_completeness_and_excluded(tmp_path):
    p = _write(tmp_path)
    res = ingest_source("dump", str(p))
    # 2 content files declared and indexed; the .png is tree-only (excluded).
    assert res.file_count == 2
    with Repo.open("dump") as r:
        meta = r.meta()
        assert meta["declared_file_count"] == 2
        assert meta["indexed_file_count"] == 2
        assert meta["complete"] is True
        assert "assets/logo.png" in meta["excluded_files"]

        v = r.verify()
        assert v["ok"] is True
        assert v["completeness"]["declared_files"] == 2
        assert v["completeness"]["indexed_files"] == 2
        assert v["completeness"]["drift_detected"] is False
        assert v["completeness"]["recheck_declared"] == 2  # source still present


def test_compress_markers_not_in_stored_content(tmp_path):
    p = _write(tmp_path)
    ingest_source("dump", str(p))
    with Repo.open("dump") as r:
        out = r.read("a.py")
        assert "\u22ee" not in out["content"]  # markers normalized out of storage
        # symbols recovered into the digest
        text, _ = r.map(level=2)
        assert "Foo" in text and "bar" in text
        # the binary file is surfaced to the agent
        assert "assets/logo.png" in text
