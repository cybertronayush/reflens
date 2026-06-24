"""Markdown heading outline — turns docs/specs/ADRs into navigable structure.

Headings become symbols (kind ``h1``..``h6``), skipping anything inside fenced
code blocks so code comments aren't mistaken for headings.
"""

from __future__ import annotations

import re

from ..models import Symbol
from .base import ExtractOutput

_ATX = re.compile(r"^(#{1,6})\s+(?P<name>.+?)\s*#*\s*$")


def extract_markdown(path: str, text: str, lang: str = "markdown") -> ExtractOutput:
    out = ExtractOutput(extractor="markdown")
    in_fence = False
    fence_tok = ""
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            tok = stripped[:3]
            if not in_fence:
                in_fence, fence_tok = True, tok
            elif tok == fence_tok:
                in_fence = False
            continue
        if in_fence:
            continue
        m = _ATX.match(line)
        if m:
            level = len(m.group(1))
            name = m.group("name").strip()
            if name:
                out.symbols.append(
                    Symbol(kind=f"h{level}", name=name,
                           signature=("#" * level) + " " + name,
                           start_line=i, end_line=i)
                )
    return out
