"""reflens — reference-repo context engine for AI coding agents.

Two-tier guarantee:
  Tier 1 (Intelligence Digest): a dense, in-context-budgeted representation of a
    repository's architecture, public symbol surface, dependency graph, and mined
    intent. Lossy on implementation bodies, complete on meaning and structure.
  Tier 2 (Lossless Store): every byte of every file retained, content-addressed,
    byte-perfect on retrieval. `reflens verify` proves it via SHA-256 round-trip.

The agent reasons from Tier 1 and expands into Tier 2 only when a task needs the
exact source. Nothing is ever destroyed to compress.
"""

from __future__ import annotations

from ._version import __version__

__all__ = ["__version__"]
