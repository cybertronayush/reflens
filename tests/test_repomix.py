from __future__ import annotations

from reflens.ingest import repomix

_DUMP = """This file is a merged representation of the entire codebase, combined into a single document by Repomix.
The content has been processed where comments have been removed, line numbers have been added.

# File Summary
Some preamble.

# Directory Structure
```
a.py
docs/b.md
```

## File: a.py
````python
def foo():
    return 1
````

## File: docs/b.md
````markdown
# Title

```js
console.log("nested triple-backtick stays intact")
```
````
"""


def _write(tmp_path):
    p = tmp_path / "dump.md"
    p.write_text(_DUMP, encoding="utf-8")
    return p


def test_detects_repomix(tmp_path):
    p = _write(tmp_path)
    assert repomix.looks_like_repomix(p)


def test_detect_transforms(tmp_path):
    p = _write(tmp_path)
    tags = repomix.detect_transforms(p)
    assert "comments_removed" in tags
    assert "line_numbers" in tags


def test_iter_files_exact_content(tmp_path):
    p = _write(tmp_path)
    files = dict(repomix.iter_repomix(p))
    assert set(files) == {"a.py", "docs/b.md"}
    assert files["a.py"] == "def foo():\n    return 1"
    # The 4-backtick fence must NOT be closed by the inner triple-backtick block.
    assert "console.log" in files["docs/b.md"]
    assert files["docs/b.md"].count("```") == 2  # the inner js fence survived intact
