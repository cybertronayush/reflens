"""Tier-1 Intelligence Digest: the dense, in-context representation.

builder.py assembles structured data from the index (+ a few small blob reads
for README/intent); serialize.py renders it as dense markdown within a token
budget, degrading by resolution level and always emitting a drill-down pointer
where it truncates — so nothing in the repo becomes unreachable.
"""

from __future__ import annotations

from .builder import build_digest
from .serialize import render_digest

__all__ = ["build_digest", "render_digest"]
