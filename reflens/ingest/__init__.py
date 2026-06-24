"""Ingest: stream a source (repomix dump, local dir, git repo) into the store.

Sources are streamed file-by-file so a 100MB+ input never loads fully into RAM —
each file is hashed, blobbed (Tier 2), outlined, and chunked, then released.
"""

from __future__ import annotations

from .base import ingest_source

__all__ = ["ingest_source"]
