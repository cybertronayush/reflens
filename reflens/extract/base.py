"""Extractor contract shared by every language backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..models import Symbol

_DOCSTRING_MAX = 240


@dataclass
class ExtractOutput:
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)  # module/path tokens, for the graph
    extractor: str = "none"  # which backend produced this (provenance)


class Extractor(Protocol):
    name: str

    def supports(self, lang: str) -> bool: ...

    def extract(self, path: str, text: str, lang: str) -> ExtractOutput: ...


def clip_doc(doc: str | None) -> str | None:
    """Normalize a docstring/leading comment to a single trimmed line."""
    if not doc:
        return None
    line = " ".join(doc.strip().split())
    if not line:
        return None
    if len(line) > _DOCSTRING_MAX:
        line = line[: _DOCSTRING_MAX - 1].rstrip() + "\u2026"
    return line
