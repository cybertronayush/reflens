"""Outline extraction: turn source text into symbols + imports (Tier-1 surface).

Strategy (chosen for correctness-without-heavy-deps):
  - Python  -> stdlib ``ast`` (exact, zero-dependency).
  - others  -> tree-sitter when the optional grammar pack is installed,
               otherwise a regex outliner that recognizes common declarations.

Every extractor returns the same ``ExtractOutput`` contract, so downstream
(graph, digest, store) never cares which one ran.
"""

from __future__ import annotations

from .base import ExtractOutput, Extractor
from .registry import detect_language, extract_outline

__all__ = ["ExtractOutput", "Extractor", "detect_language", "extract_outline"]
