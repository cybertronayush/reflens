"""Regex outliner for languages without a dedicated/installed AST backend.

Heuristic but robust: recognizes the common declaration forms per language
family. ``end_line`` equals ``start_line`` (scope isn't tracked); the agent uses
``start_line`` to ``reflens_read`` the exact body from Tier 2 when needed.
"""

from __future__ import annotations

import re

from ..models import Symbol
from .base import ExtractOutput

# Each entry: (kind, compiled regex with a named group `name`).
_C_LIKE = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+)*class\s+(?P<name>[A-Za-z_]\w*)")),
    ("interface", re.compile(r"^\s*(?:export\s+)?(?:public\s+)?interface\s+(?P<name>[A-Za-z_]\w*)")),
    ("enum", re.compile(r"^\s*(?:export\s+)?(?:public\s+)?enum\s+(?P<name>[A-Za-z_]\w*)")),
]

_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    # Used only as a fallback when ast.parse fails (e.g. repomix --compress dumps
    # where bodies are stripped, so the source isn't valid Python).
    "python": [
        ("class", re.compile(r"^\s*class\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)")),
        ("const", re.compile(r"^(?P<name>[A-Z][A-Z0-9_]{1,})\s*[:=]")),
    ],
    "javascript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_$]\w*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(?P<name>[A-Za-z_$]\w*)")),
        # arrow-function binding (classified as function); must precede generic const
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$]\w*)\s*=.*=>")),
        # any exported binding (config objects, instances, constants)
        ("const", re.compile(r"^\s*export\s+(?:const|let|var)\s+(?P<name>[A-Za-z_$]\w*)")),
    ],
    "typescript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$]\w*)")),
        ("interface", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?interface\s+(?P<name>[A-Za-z_$]\w*)")),
        ("enum", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+(?P<name>[A-Za-z_$]\w*)")),
        ("type", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?type\s+(?P<name>[A-Za-z_$]\w*)")),
        ("namespace", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:namespace|module)\s+(?P<name>[A-Za-z_$][\w.]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\*?\s+(?P<name>[A-Za-z_$]\w*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$]\w*)\s*[:=].*=>")),
        ("const", re.compile(r"^\s*export\s+(?:declare\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$]\w*)")),
    ],
    "go": [
        ("function", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")),
        ("type", re.compile(r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+(?:struct|interface)\b")),
        ("type", re.compile(r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+\w")),
    ],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<name>[A-Za-z_]\w*)")),
        ("struct", re.compile(r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?struct\s+(?P<name>[A-Za-z_]\w*)")),
        ("enum", re.compile(r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?enum\s+(?P<name>[A-Za-z_]\w*)")),
        ("trait", re.compile(r"^\s*(?:pub\s+(?:\([^)]*\)\s*)?)?trait\s+(?P<name>[A-Za-z_]\w*)")),
        ("impl", re.compile(r"^\s*impl(?:<[^>]*>)?\s+(?P<name>[A-Za-z_][\w:<>, ]*)")),
        ("macro", re.compile(r"^\s*macro_rules!\s+(?P<name>[A-Za-z_]\w*)")),
    ],
    "java": [
        *_C_LIKE,
        ("method", re.compile(r"^\s*(?:public|private|protected)\s+(?:static\s+|final\s+|abstract\s+|synchronized\s+)*[\w<>\[\].]+\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
        ("record", re.compile(r"^\s*(?:public\s+)?record\s+(?P<name>[A-Za-z_]\w*)")),
    ],
    "kotlin": [
        ("class", re.compile(r"^\s*(?:open\s+|abstract\s+|sealed\s+|data\s+)*class\s+(?P<name>[A-Za-z_]\w*)")),
        ("interface", re.compile(r"^\s*interface\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:suspend\s+)?fun\s+(?:<[^>]*>\s+)?(?P<name>[A-Za-z_]\w*)\s*\(")),
        ("object", re.compile(r"^\s*object\s+(?P<name>[A-Za-z_]\w*)")),
    ],
    "c": [
        ("function", re.compile(r"^[A-Za-z_][\w\s\*]*\s+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$")),
        ("struct", re.compile(r"^\s*(?:typedef\s+)?struct\s+(?P<name>[A-Za-z_]\w*)")),
    ],
    "cpp": [
        ("class", re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?class\s+(?P<name>[A-Za-z_]\w*)")),
        ("struct", re.compile(r"^\s*struct\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"^[A-Za-z_][\w\s\*:<>,&]*\s+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*(?:const)?\s*\{?\s*$")),
    ],
    "csharp": [
        *_C_LIKE,
        ("struct", re.compile(r"^\s*(?:public\s+|internal\s+)?struct\s+(?P<name>[A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:public|private|protected|internal)\s+(?:static\s+|virtual\s+|override\s+|async\s+)*[\w<>\[\].]+\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ],
    "ruby": [
        ("class", re.compile(r"^\s*class\s+(?P<name>[A-Z]\w*)")),
        ("module", re.compile(r"^\s*module\s+(?P<name>[A-Z]\w*)")),
        ("function", re.compile(r"^\s*def\s+(?P<name>[A-Za-z_]\w*[!?=]?)")),
    ],
    "php": [
        ("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(?P<name>[A-Za-z_]\w*)")),
        ("interface", re.compile(r"^\s*interface\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+(?P<name>[A-Za-z_]\w*)")),
    ],
    "swift": [
        ("class", re.compile(r"^\s*(?:public\s+|open\s+|final\s+)*class\s+(?P<name>[A-Za-z_]\w*)")),
        ("struct", re.compile(r"^\s*(?:public\s+)?struct\s+(?P<name>[A-Za-z_]\w*)")),
        ("protocol", re.compile(r"^\s*(?:public\s+)?protocol\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:public\s+|private\s+|static\s+)*func\s+(?P<name>[A-Za-z_]\w*)")),
        ("enum", re.compile(r"^\s*(?:public\s+)?enum\s+(?P<name>[A-Za-z_]\w*)")),
    ],
    "scala": [
        ("class", re.compile(r"^\s*(?:abstract\s+|final\s+|sealed\s+|case\s+)*class\s+(?P<name>[A-Za-z_]\w*)")),
        ("object", re.compile(r"^\s*(?:case\s+)?object\s+(?P<name>[A-Za-z_]\w*)")),
        ("trait", re.compile(r"^\s*trait\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*def\s+(?P<name>[A-Za-z_]\w*)")),
    ],
    "shell": [
        ("function", re.compile(r"^\s*(?:function\s+)?(?P<name>[A-Za-z_]\w*)\s*\(\)\s*\{")),
    ],
}

# Treat jsx/tsx like their base language.
_PATTERNS["tsx"] = _PATTERNS["typescript"]
_PATTERNS["jsx"] = _PATTERNS["javascript"]

_IMPORTS: dict[str, list[re.Pattern]] = {
    "python": [
        re.compile(r"^\s*import\s+(?P<m>[\w.]+)"),
        re.compile(r"^\s*from\s+(?P<m>[.\w]+)\s+import"),
    ],
    "javascript": [
        re.compile(r"""import\s+.*?from\s+['"](?P<m>[^'"]+)['"]"""),
        re.compile(r"""require\(\s*['"](?P<m>[^'"]+)['"]\s*\)"""),
    ],
    "go": [re.compile(r"""^\s*(?:_\s+|\w+\s+)?['"](?P<m>[^'"]+)['"]""")],
    "rust": [re.compile(r"^\s*use\s+(?P<m>[A-Za-z_][\w:]*)")],
    "java": [re.compile(r"^\s*import\s+(?:static\s+)?(?P<m>[\w.]+)")],
    "csharp": [re.compile(r"^\s*using\s+(?:static\s+)?(?P<m>[\w.]+)")],
    "ruby": [re.compile(r"""^\s*require(?:_relative)?\s+['"](?P<m>[^'"]+)['"]""")],
    "php": [re.compile(r"^\s*use\s+(?P<m>[\w\\]+)")],
    "c": [re.compile(r"""^\s*#include\s+[<"](?P<m>[^>"]+)[>"]""")],
    "cpp": [re.compile(r"""^\s*#include\s+[<"](?P<m>[^>"]+)[>"]""")],
    "swift": [re.compile(r"^\s*import\s+(?P<m>[\w.]+)")],
    "scala": [re.compile(r"^\s*import\s+(?P<m>[\w.]+)")],
}
_IMPORTS["typescript"] = _IMPORTS["javascript"]
_IMPORTS["tsx"] = _IMPORTS["javascript"]
_IMPORTS["jsx"] = _IMPORTS["javascript"]

# Applied to any language we don't have a tuned set for.
_GENERIC = [
    ("class", re.compile(r"^\s*(?:export\s+|public\s+)?class\s+(?P<name>[A-Za-z_]\w*)")),
    ("function", re.compile(r"^\s*(?:export\s+|public\s+)?(?:async\s+)?(?:func|function|fn|def)\s+(?P<name>[A-Za-z_]\w*)")),
    ("struct", re.compile(r"^\s*struct\s+(?P<name>[A-Za-z_]\w*)")),
    ("interface", re.compile(r"^\s*interface\s+(?P<name>[A-Za-z_]\w*)")),
]

_MAX_SIG = 200


def extract_regex(path: str, text: str, lang: str) -> ExtractOutput:
    out = ExtractOutput(extractor=f"regex:{lang}")
    patterns = _PATTERNS.get(lang, _GENERIC)
    import_pats = _IMPORTS.get(lang, [])
    seen: set[tuple[str, int]] = set()
    for i, line in enumerate(text.splitlines(), start=1):
        for kind, pat in patterns:
            m = pat.search(line)
            if not m:
                continue
            name = m.group("name")
            key = (name, i)
            if key in seen:
                continue
            seen.add(key)
            sig = line.strip()
            if len(sig) > _MAX_SIG:
                sig = sig[: _MAX_SIG - 1] + "\u2026"
            out.symbols.append(
                Symbol(kind=kind, name=name, signature=sig, start_line=i, end_line=i)
            )
            break  # one symbol kind per line
        for pat in import_pats:
            m = pat.search(line)
            if m:
                out.imports.append(m.group("m"))
    return out
