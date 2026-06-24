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


def test_clean_content_strips_compress_markers():
    raw = "class Tokenizer\n\u22ee----\ndef count(self)\n\u22ee----\n"
    cleaned = repomix.clean_content(raw)
    assert "\u22ee" not in cleaned
    assert "class Tokenizer" in cleaned
    assert "def count(self)" in cleaned


def test_clean_content_strips_line_numbers_when_majority():
    raw = "1: import os\n2: def f():\n3:     return 1\n4: x = 2"
    cleaned = repomix.clean_content(raw)
    assert cleaned == "import os\ndef f():\n    return 1\nx = 2"


def test_clean_content_keeps_real_numeric_code():
    # No line is numeric-prefixed; content (incl. a dict with int keys) is untouched.
    raw = "def f():\n    d = {1: 'a', 2: 'b'}\n    return d"
    cleaned = repomix.clean_content(raw)
    assert cleaned == raw


def test_count_entries(tmp_path):
    p = _write(tmp_path)
    assert repomix.count_entries(p) == 2


def test_parse_directory_structure(tmp_path):
    dump = (
        "Repomix packed representation.\n\n"
        "# Directory Structure\n"
        "```\n"
        "a.py\n"
        "assets/logo.png\n"
        "pkg/\n"
        "  mod.py\n"
        "```\n\n"
        "## File: a.py\n````python\nx=1\n````\n"
    )
    p = tmp_path / "d.md"
    p.write_text(dump, encoding="utf-8")
    files = repomix.parse_directory_structure(p)
    assert set(files) == {"a.py", "assets/logo.png", "pkg/mod.py"}
