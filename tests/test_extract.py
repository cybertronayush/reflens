from __future__ import annotations

from reflens.extract import detect_language, extract_outline


def test_python_exact():
    src = (
        '"""mod."""\n'
        "import os\n"
        "from .x import y\n"
        "CONST = 1\n"
        "class A(Base):\n"
        '    """doc a."""\n'
        "    def m(self, n: int = 2) -> str: ...\n"
        "async def go() -> None: ...\n"
    )
    o = extract_outline("m.py", src)
    assert o.extractor == "python-ast"
    kinds = {(s.kind, s.name) for s in o.symbols}
    assert ("class", "A") in kinds
    assert ("method", "m") in kinds
    assert ("function", "go") in kinds
    assert ("const", "CONST") in kinds
    assert "os" in o.imports and ".x" in o.imports
    method = next(s for s in o.symbols if s.name == "m")
    assert method.parent == "A"
    assert "-> str" in method.signature


def test_python_syntax_error_degrades():
    o = extract_outline("bad.py", "def (= : not python\nimport real_mod\n")
    # still scans imports, doesn't raise
    assert "real_mod" in o.imports


def test_regex_typescript():
    o = extract_outline("s.ts", "export class S {}\nexport const F = async () => {}\ninterface I {}\n")
    names = {s.name for s in o.symbols}
    assert {"S", "F", "I"} <= names


def test_language_detection():
    assert detect_language("a/b.py") == "python"
    assert detect_language("Dockerfile") == "dockerfile"
    assert detect_language("x.tsx") == "tsx"
    assert detect_language("unknown.zzz") == "text"
