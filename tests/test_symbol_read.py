"""reflens_read on a symbol must return the body, not just the declaration line —
even for regex-extracted languages where end_line == start_line."""

from __future__ import annotations

from reflens.engine import Repo
from reflens.ingest import ingest_source


def test_point_symbol_read_returns_body(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "svc.ts").write_text(
        "export class Service {\n"
        "  run() {\n"
        "    return 1;\n"
        "  }\n"
        "}\n"
        "export function helper() {\n"
        "  return 2;\n"
        "}\n",
        encoding="utf-8",
    )
    ingest_source("ts", str(root))
    with Repo.open("ts") as r:
        res = r.read("Service")
        assert res["kind"] == "symbol"
        body = res["bodies"][0]
        # multiple lines (bounded by the next symbol), not a single decl line
        assert body["end_line"] > body["start_line"]
        assert "run()" in body["content"]
        assert "return 1" in body["content"]
        # must stop before the next symbol (helper on line 6)
        assert "helper" not in body["content"]


def test_python_symbol_keeps_exact_extent(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "m.py").write_text(
        "def a():\n    x = 1\n    return x\n\ndef b():\n    return 2\n", encoding="utf-8"
    )
    ingest_source("py", str(root))
    with Repo.open("py") as r:
        body = r.read("a")["bodies"][0]
        # ast gives the real extent: a() is lines 1-3, must not bleed into b()
        assert "return x" in body["content"]
        assert "def b" not in body["content"]
