"""Language detection + extractor selection with graceful degradation.

Selection order:
  python              -> stdlib ast (exact)
  other code langs    -> tree-sitter (only if REFLENS_USE_TREESITTER=1 and the
                         optional pack imports) else regex
  data/text langs     -> no symbol extraction (imports may still be scanned)

Any extractor failure falls back to regex, then to empty — never raises.
"""

from __future__ import annotations

import os

from .base import ExtractOutput
from .python_ast import extract_python
from .regex_fallback import extract_regex

# Extension (lowercase, no dot) -> language id.
_EXT_LANG: dict[str, str] = {
    "py": "python", "pyi": "python", "pyw": "python",
    "js": "javascript", "mjs": "javascript", "cjs": "javascript",
    "jsx": "jsx",
    "ts": "typescript", "mts": "typescript", "cts": "typescript",
    "tsx": "tsx",
    "go": "go",
    "rs": "rust",
    "java": "java",
    "kt": "kotlin", "kts": "kotlin",
    "c": "c", "h": "c",
    "cc": "cpp", "cpp": "cpp", "cxx": "cpp", "hpp": "cpp", "hh": "cpp", "hxx": "cpp",
    "cs": "csharp",
    "rb": "ruby",
    "php": "php",
    "swift": "swift",
    "scala": "scala", "sc": "scala",
    "sh": "shell", "bash": "shell", "zsh": "shell",
    # data / docs (no symbol extraction, still chunked + stored losslessly)
    "md": "markdown", "markdown": "markdown", "mdx": "markdown",
    "json": "json", "jsonl": "json",
    "yaml": "yaml", "yml": "yaml",
    "toml": "toml",
    "ini": "ini", "cfg": "ini",
    "xml": "xml", "html": "html", "htm": "html", "css": "css", "scss": "css",
    "sql": "sql",
    "txt": "text", "rst": "text", "csv": "text", "tsv": "text",
    "proto": "proto", "graphql": "graphql", "gql": "graphql",
    "dockerfile": "dockerfile", "makefile": "makefile",
}

# Languages we attempt to extract code symbols from.
_CODE_LANGS = {
    "python", "javascript", "jsx", "typescript", "tsx", "go", "rust", "java",
    "kotlin", "c", "cpp", "csharp", "ruby", "php", "swift", "scala", "shell",
}

_SPECIAL_BASENAMES = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
    "cmakelists.txt": "cmake",
    "go.mod": "gomod",
    "cargo.toml": "toml",
    "package.json": "json",
}


def detect_language(path: str) -> str:
    base = path.rsplit("/", 1)[-1].lower()
    if base in _SPECIAL_BASENAMES:
        return _SPECIAL_BASENAMES[base]
    if "." not in base:
        return "text"
    ext = base.rsplit(".", 1)[-1]
    return _EXT_LANG.get(ext, "text")


def is_code_lang(lang: str) -> bool:
    return lang in _CODE_LANGS


def extract_outline(path: str, text: str, lang: str | None = None) -> ExtractOutput:
    lang = lang or detect_language(path)
    if lang == "python":
        try:
            return extract_python(path, text, lang)
        except Exception:
            return extract_regex(path, text, "python")
    if lang == "markdown":
        from .markdown import extract_markdown

        return extract_markdown(path, text, lang)
    if lang not in _CODE_LANGS:
        return ExtractOutput(extractor="none")
    if os.environ.get("REFLENS_USE_TREESITTER", "").strip() in ("1", "on", "true"):
        try:
            from .treesitter import try_extract_treesitter

            ts = try_extract_treesitter(path, text, lang)
            if ts is not None and ts.symbols:
                return ts
        except Exception:
            pass
    try:
        return extract_regex(path, text, lang)
    except Exception:
        return ExtractOutput(extractor="none")
