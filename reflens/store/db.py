"""SQLite index: files, symbols, chunks, edges, embeddings, and FTS5 search.

Design choices:
  - FTS5 tables are *standalone* (not external-content) with an explicit
    ``rowid`` equal to the source row id. This trades a little disk for zero
    sync-trigger complexity. Re-ingest drops and rebuilds the DB, so we never
    UPDATE/DELETE indexed rows in place — keeping the model simple and correct.
  - All write paths are parameterized; the FTS MATCH query is sanitized by
    quoting every token, neutralizing FTS5 operators (injection guard).
  - WAL + NORMAL synchronous: fast bulk ingest, safe enough for a local cache.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from ..models import Chunk, Edge, FileRecord, Symbol

SCHEMA_VERSION = 2

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,
    lang       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    line_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_lang ON files(lang);

CREATE TABLE IF NOT EXISTS symbols (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    signature  TEXT NOT NULL,
    parent     TEXT,
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL,
    docstring  TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);

CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ord        INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL,
    text       TEXT NOT NULL,
    token_est  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);

CREATE TABLE IF NOT EXISTS edges (
    id   INTEGER PRIMARY KEY,
    src  TEXT NOT NULL,
    dst  TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);

-- Semantic index. `unit` is the kind of embedded thing ('symbol' by default —
-- the dense signature+docstring surface; 'chunk' is supported for back-compat /
-- body-level embeddings). Keyed by (unit, unit_id) so the index granularity can
-- change without a schema migration.
CREATE TABLE IF NOT EXISTS embeddings (
    unit    TEXT NOT NULL,
    unit_id INTEGER NOT NULL,
    dim     INTEGER NOT NULL,
    vec     BLOB NOT NULL,
    PRIMARY KEY (unit, unit_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
    USING fts5(text, tokenize='unicode61 remove_diacritics 2');

CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts
    USING fts5(name, signature, docstring, tokenize='unicode61 remove_diacritics 2');
"""


def sanitize_fts_query(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Each whitespace token is wrapped in double quotes (with internal quotes
    doubled), then joined with implicit-AND. This neutralizes FTS5 operators
    (``*``, ``:``, ``-``, ``OR``, ``NEAR``, parentheses) so a query can never
    raise a syntax error or alter matching semantics unexpectedly.
    Returns "" if there is nothing searchable.
    """
    tokens = [t for t in query.replace("\n", " ").split(" ") if t.strip()]
    quoted = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        quoted.append('"' + t.replace('"', '""') + '"')
    return " ".join(quoted)


class Database:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def open(cls, path: Path, *, create: bool = False) -> "Database":
        path = Path(path)
        if not create and not path.exists():
            raise FileNotFoundError(f"no index at {path} (ingest first)")
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        db = cls(conn)
        if create:
            db.init_schema()
        return db

    def init_schema(self) -> None:
        self.conn.executescript(_DDL)
        self.set_meta("schema_version", SCHEMA_VERSION)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- meta ------------------------------------------------------------
    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def all_meta(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key,value FROM meta").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    # ---- writes ----------------------------------------------------------
    def insert_file(self, f: FileRecord) -> int:
        cur = self.conn.execute(
            "INSERT INTO files(path,lang,sha256,size_bytes,line_count) VALUES(?,?,?,?,?)",
            (f.path, f.lang, f.sha256, f.size_bytes, f.line_count),
        )
        return int(cur.lastrowid)

    def insert_symbols(self, file_id: int, symbols: Iterable[Symbol]) -> int:
        n = 0
        for s in symbols:
            cur = self.conn.execute(
                "INSERT INTO symbols(file_id,kind,name,signature,parent,start_line,end_line,docstring)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (file_id, s.kind, s.name, s.signature, s.parent, s.start_line, s.end_line, s.docstring),
            )
            sid = int(cur.lastrowid)
            self.conn.execute(
                "INSERT INTO symbols_fts(rowid,name,signature,docstring) VALUES(?,?,?,?)",
                (sid, s.name, s.signature, s.docstring or ""),
            )
            n += 1
        return n

    def insert_chunks(self, file_id: int, chunks: Iterable[Chunk]) -> int:
        n = 0
        for c in chunks:
            cur = self.conn.execute(
                "INSERT INTO chunks(file_id,ord,start_line,end_line,text,token_est) VALUES(?,?,?,?,?,?)",
                (file_id, c.ord, c.start_line, c.end_line, c.text, c.token_est),
            )
            cid = int(cur.lastrowid)
            self.conn.execute(
                "INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cid, c.text)
            )
            n += 1
        return n

    def insert_edges(self, edges: Iterable[Edge]) -> int:
        n = 0
        for e in edges:
            self.conn.execute(
                "INSERT INTO edges(src,dst,kind) VALUES(?,?,?)", (e.src, e.dst, e.kind)
            )
            n += 1
        return n

    def set_embedding(self, unit: str, unit_id: int, dim: int, vec: bytes) -> None:
        self.conn.execute(
            "INSERT INTO embeddings(unit,unit_id,dim,vec) VALUES(?,?,?,?) "
            "ON CONFLICT(unit,unit_id) DO UPDATE SET dim=excluded.dim, vec=excluded.vec",
            (unit, unit_id, dim, vec),
        )

    def commit(self) -> None:
        self.conn.commit()

    # ---- reads -----------------------------------------------------------
    def file_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"])

    def list_files(self, *, lang: Optional[str] = None) -> list[sqlite3.Row]:
        if lang:
            return self.conn.execute(
                "SELECT * FROM files WHERE lang=? ORDER BY path", (lang,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM files ORDER BY path").fetchall()

    def get_file_by_path(self, path: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def lang_breakdown(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT lang, COUNT(*) files, SUM(size_bytes) bytes, SUM(line_count) lines "
            "FROM files GROUP BY lang ORDER BY bytes DESC"
        ).fetchall()

    def symbols_for_file(self, file_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM symbols WHERE file_id=? ORDER BY start_line", (file_id,)
        ).fetchall()

    def symbol_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"])

    def find_symbols_by_name(self, name: str, *, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.*, f.path AS path FROM symbols s JOIN files f ON f.id=s.file_id "
            "WHERE s.name=? ORDER BY f.path LIMIT ?",
            (name, limit),
        ).fetchall()

    def chunks_for_file(self, file_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM chunks WHERE file_id=? ORDER BY ord", (file_id,)
        ).fetchall()

    def get_chunk(self, chunk_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()

    def iter_all_chunks(self) -> Iterable[sqlite3.Row]:
        yield from self.conn.execute(
            "SELECT c.id, c.file_id, c.start_line, c.end_line, c.text, f.path AS path "
            "FROM chunks c JOIN files f ON f.id=c.file_id ORDER BY c.id"
        )

    def search_chunks_fts(self, query: str, *, limit: int = 10) -> list[sqlite3.Row]:
        match = sanitize_fts_query(query)
        if not match:
            return []
        return self.conn.execute(
            "SELECT c.id, f.path AS path, c.start_line, c.end_line, c.text, "
            "       bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
            "JOIN files f ON f.id=c.file_id "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit),
        ).fetchall()

    def search_symbols_fts(self, query: str, *, limit: int = 10) -> list[sqlite3.Row]:
        match = sanitize_fts_query(query)
        if not match:
            return []
        return self.conn.execute(
            "SELECT s.id, s.name, s.kind, s.signature, s.start_line, s.end_line, "
            "       f.path AS path, bm25(symbols_fts) AS rank "
            "FROM symbols_fts JOIN symbols s ON s.id=symbols_fts.rowid "
            "JOIN files f ON f.id=s.file_id "
            "WHERE symbols_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit),
        ).fetchall()

    def edges_from(self, src: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM edges WHERE src=? ORDER BY dst", (src,)
        ).fetchall()

    def edges_to(self, dst: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM edges WHERE dst=? ORDER BY src", (dst,)
        ).fetchall()

    def edge_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"])

    def iter_embeddings(self) -> Iterable[sqlite3.Row]:
        # Defensive: an index built by an older schema version (chunk-keyed) lacks
        # the `unit` column; degrade to no-semantic until re-ingested.
        try:
            yield from self.conn.execute("SELECT unit, unit_id, dim, vec FROM embeddings")
        except sqlite3.OperationalError:
            return

    def has_embeddings(self) -> bool:
        try:
            row = self.conn.execute(
                "SELECT 1 FROM embeddings WHERE unit IS NOT NULL LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None

    def file_signature(self):
        """(path, signature) of the backing DB file, for cache invalidation.

        The signature (mtime_ns, size, wal-size) changes when the index is
        re-ingested (atomic swap replaces the file), so caches keyed on it
        invalidate automatically. Returns (None, None) for in-memory/odd DBs.
        """
        try:
            path = None
            for row in self.conn.execute("PRAGMA database_list"):
                if row[1] == "main" and row[2]:
                    path = row[2]
                    break
            if not path:
                return None, None
            st = os.stat(path)
            wal = path + "-wal"
            wal_sz = os.path.getsize(wal) if os.path.exists(wal) else 0
            return path, (st.st_mtime_ns, st.st_size, wal_sz)
        except OSError:
            return None, None

    def symbols_by_ids(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        """Map symbol ids -> row (with file path) for semantic result rendering."""
        if not ids:
            return {}
        ph = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT s.id, s.name, s.kind, s.signature, s.start_line, s.end_line, "
            f"f.path AS path FROM symbols s JOIN files f ON f.id=s.file_id "
            f"WHERE s.id IN ({ph})",
            ids,
        ).fetchall()
        return {int(r["id"]): r for r in rows}
