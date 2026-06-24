from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point REFLENS_HOME at a temp dir so tests never touch real state."""
    monkeypatch.setenv("REFLENS_HOME", str(tmp_path / "reflens-home"))
    yield


@pytest.fixture
def sample_repo(tmp_path):
    root = tmp_path / "src"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(
        '"""Core module."""\n'
        "import os\n"
        "from .util import helper\n"
        "API_KEY = 'x'\n"
        "class Engine:\n"
        '    """Runs things."""\n'
        "    def run(self, n: int) -> int:\n"
        "        return helper(n)\n"
        "def main() -> None:\n"
        "    Engine().run(3)\n",
        encoding="utf-8",
    )
    (root / "pkg" / "util.py").write_text("def helper(x):\n    return x * 2\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n\nA demo repo.\n", encoding="utf-8")
    # a unicode + tricky-content file to stress losslessness
    (root / "notes.txt").write_text("café \u00e9 \U0001f600\nline2\ttab\n```fenced```\n", encoding="utf-8")
    return root
