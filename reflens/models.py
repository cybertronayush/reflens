"""Shared data contracts.

These dataclasses are the stable seam every module builds against: ingest writes
them, store persists them, extract/graph/search/digest read them. Keep this file
dependency-free and change it deliberately — it is the schema the whole system
agrees on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class RepoMeta:
    name: str
    source_kind: str  # "repomix" | "dir" | "git"
    source_ref: str  # original path / url
    ingested_at: str  # ISO-8601 UTC
    commit_sha: Optional[str]
    file_count: int
    total_bytes: int
    config: dict  # ingest-time config (semantic on/off, extractor versions, etc.)


@dataclass
class FileRecord:
    path: str  # repo-relative, POSIX separators
    lang: str  # detected language id (e.g. "python", "rust", "typescript", "text")
    sha256: str  # of the exact original bytes
    size_bytes: int
    line_count: int
    id: Optional[int] = None


@dataclass
class Symbol:
    kind: str  # "class" | "function" | "method" | "interface" | "type" | "const" | ...
    name: str
    signature: str  # one-line declaration, no body
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    parent: Optional[str] = None  # enclosing symbol name (for methods/nested)
    docstring: Optional[str] = None  # first doc/leading-comment line(s), truncated
    file_id: Optional[int] = None
    id: Optional[int] = None


@dataclass
class Chunk:
    ord: int  # 0-indexed position within the file
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    text: str  # exact slice of the original (lossless: union of chunks == file)
    token_est: int
    file_id: Optional[int] = None
    id: Optional[int] = None


@dataclass(frozen=True)
class Edge:
    src: str  # source file path (or qualified symbol)
    dst: str  # target module/file path (or qualified symbol)
    kind: str  # "import" | "call" | "reference"


@dataclass
class Hit:
    path: str
    start_line: int
    end_line: int
    score: float
    snippet: str
    kind: str = "chunk"  # "chunk" | "symbol"
    name: Optional[str] = None  # symbol name when kind == "symbol"
    source: str = "lexical"  # "lexical" | "semantic" | "hybrid"


@dataclass
class IngestResult:
    name: str
    file_count: int
    total_bytes: int
    symbol_count: int
    chunk_count: int
    edge_count: int
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    embedded_symbols: int = 0
    reused_embeddings: int = 0
