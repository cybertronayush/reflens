# Architecture

reflens turns a reference repository (local dir, git URL, or Repomix dump) into a
**queryable knowledge source** an AI coding agent consumes over MCP. This document
explains the design, the contracts between modules, the data model, and the key
decisions and their tradeoffs.

---

## 1. The core thesis: two tiers

A large repo (often 100k–25M tokens) cannot fit in a model context window
losslessly. reflens splits the problem instead of pretending it doesn't exist:

```
                          ┌──────────────────────────────────────────┐
   source                 │  Tier 1 — Intelligence Digest (in-context)│
 (dir / git URL /         │  architecture · module centrality ·        │
  repomix .md)            │  entry points · conventions · decisions ·  │
       │   ingest         │  full symbol surface (signatures+docstrings)│
       ▼   (streaming)    │  budgeted, multi-resolution, truncation     │
 ┌───────────────┐        │  pointers — never silent loss               │
 │  reflens add  │───────▶├──────────────────────────────────────────┤
 └───────────────┘        │  Tier 2 — Lossless Store (on disk)         │
                          │  every byte, content-addressed (gzip+sha256)│
                          │  byte-exact retrieval; `verify` proves it   │
                          └──────────────────────────────────────────┘
       │   MCP stdio (JSON-RPC)
       ▼
 OpenCode / Claude Code  →  list · modules · map · search · read · neighbors · history · verify
```

The agent **reasons from Tier 1** and **expands into Tier 2 on demand**. Retrieval
is the safety layer, not the primary mechanism.

---

## 2. Decomposition map

Each module has a stable contract; they were built (and can be changed) largely
independently behind these seams.

| Module | Responsibility | Stable contract |
|---|---|---|
| `models.py` | Shared dataclasses (the schema everything agrees on) | `FileRecord`, `Symbol`, `Chunk`, `Edge`, `Hit`, `IngestResult` |
| `paths.py` | Local state layout + repo-name slug/traversal guard | `repo_dir`, `db_path`, `blobs_dir`, `slugify_name` |
| `store/blobs.py` | Tier-2 lossless content-addressed blob store | `put(bytes)->sha`, `get(sha)->bytes` (re-verifies hash) |
| `store/db.py` | SQLite index + FTS5 + injection-safe queries | `insert_*`, `search_*_fts`, `list_files`, … |
| `ingest/` | Source → store (streaming, atomic) | `ingest_source(name, source, …) -> IngestResult` |
| `extract/` | Source text → symbols + imports | `extract_outline(path, text) -> ExtractOutput` |
| `graph/` | Import edges → internal dependency centrality / neighbors | `internal_dependents(db)`, `neighbors(db, target)` |
| `search/` | Lexical + semantic retrieval, fused | `search(db, query, …) -> [Hit]` |
| `digest/` | Tier-1 builder + budgeted serializer | `build_digest`, `render_digest` |
| `engine.py` | The `Repo` facade CLI + MCP both call | `Repo.open(name)` → `map/search/read/neighbors/history/verify/modules` |
| `mcp/server.py` | stdlib JSON-RPC stdio MCP server | `initialize`, `tools/list`, `tools/call` |
| `cli/` | Command-line interface + host registrars | `add/list/map/search/read/.../install/serve` |

The **`Repo` facade is the single seam** the CLI and the MCP server both use, so
they can never drift in behavior.

---

## 3. Data flow

**Ingest** (`ingest/base.py`) streams the source file-by-file so memory stays flat
on 100MB+ inputs:

```
for (path, bytes, text) in source:
    sha   = blobs.put(bytes)                  # Tier 2 (lossless)
    fid   = db.insert_file(FileRecord(...))
    syms  = extract_outline(path, text)       # Tier 1 surface
    db.insert_symbols(fid, syms); db.insert_edges(import edges)
    db.insert_chunks(fid, chunk_text(text))   # retrieval units + FTS
commit every 400 files
(optional) embed chunks in batches  →  embeddings table
write meta (counts, completeness, git history, config)
```

**Query** (via `Repo`): `map` builds the digest from the index (+ a few small blob
reads for README/intent); `search` runs FTS5 ± vectors; `read` pulls verbatim
bytes from the blob store and slices the requested lines/symbol.

---

## 4. Data model (SQLite)

Local layout: `~/.reflens/repos/<name>/` → `index.db` + `blobs/ab/cd/<sha256>.gz`.

```sql
files     (id, path UNIQUE, lang, sha256, size_bytes, line_count)
symbols   (id, file_id→files, kind, name, signature, parent, start_line, end_line, docstring)
chunks    (id, file_id→files, ord, start_line, end_line, text, token_est)
edges     (id, src, dst, kind)                     -- raw import tokens
embeddings(chunk_id→chunks PK, dim, vec BLOB)       -- float32, L2-normalized; only when --semantic
meta      (key PK, value)                           -- json kv: counts, completeness, config, git
chunks_fts  USING fts5(text)                        -- rowid = chunks.id
symbols_fts USING fts5(name, signature, docstring)  -- rowid = symbols.id
```

- **FTS5** tables are standalone with explicit `rowid` = source id (no sync triggers; re-ingest rebuilds).
- **Blobs** are gzip-compressed, content-addressed; identical bytes stored once; `get()` re-hashes and refuses corrupt reads.
- **Lossless cover:** the union of a file's chunks covers every line (gap-free), and the blob is the byte-exact original.

---

## 5. Ingest: streaming + crash-/concurrency-safe

- **Sources:** local dir (git → `git ls-files`, respecting `.gitignore` + pruning vendored dirs like `node_modules`; non-git → `os.walk` + prune set), remote git URL (shallow `--depth 50` clone to a temp dir, then cleaned up), or a Repomix `.md` dump (streaming parser handling N-backtick fences + `⋮----` compress markers + auto-detected line-number prefixes).
- **Atomic rebuild:** a re-ingest builds the new index in a temp dir and **atomically renames it into place** at the end. A live MCP server querying the repo never sees a half-built or missing index. Temp dirs are cleaned on failure; the swap rolls back on error.
- **Memory:** flat regardless of repo size (verified: a 127 MB / 952k-symbol corpus ingests at ~145 MB peak RSS).

---

## 6. Extraction strategy

Pluggable per language, chosen for correctness without heavy dependencies:

- **Python** → stdlib `ast` (exact: classes, methods, functions, ALL-CAPS constants, **module docstrings**, imports incl. relative). On `SyntaxError` (e.g. a compressed dump), falls back to regex so symbols are still recovered.
- **Other code langs** → a tuned regex outliner (handles `export const x = …`, `export default`, `declare`, namespaces, etc.). Optional `tree-sitter` (`[code]` extra) for exact spans.
- **Markdown** → ATX heading outline (skips fenced code).
- Failure is always graceful — an extractor never raises into ingest.

Exact extractors yield real `end_line`; regex/markdown emit *point* symbols
(`end == start`). On `read`, a point symbol's body is bounded by the **next
symbol's start line** (capped) so a non-Python symbol returns its implementation,
not one declaration line.

---

## 7. Dependency graph + internal centrality

Import edges are stored as raw tokens; the architecture brief needs to know which
of the repo's **own** modules are most depended on (not that everyone imports
`pytest`). `graph/resolve.py` resolves tokens to internal files:

- Python dotted (`pkg.mod`), relative (`.mod`, `..pkg`), Rust `crate::`, TS relative (`./x`) → internal path.
- **Stdlib-collision guard:** a bare single-segment key (`logging`, `json`, `types`, `config`) is registered **only for root-level modules** — a bare `import logging` can resolve to a root `logging.py` but never to a deep `a/b/logging.py`. This prevents stdlib imports from hijacking centrality (without it, `import logging` falsely credited a deep `agent_evals/logging.py` with 197 dependents).

`internal_dependents(db)` then ranks the real hubs (`proxy/server.py`,
`cli/main.py`, `content_router.py`, …).

---

## 8. Search: lexical + semantic, fused

- **Lexical (always):** SQLite FTS5 + `bm25`. Queries are sanitized by quoting every token (neutralizes FTS5 operators → no syntax errors, no injection).
- **Semantic (opt-in):** `fastembed` (ONNX, no torch) embeds chunks; vectors are L2-normalized float32, so cosine = dot product. The full matrix is **cached in-process per DB file** (signature = mtime+size+WAL), invalidated automatically on re-ingest — a repeat semantic query drops from ~575 ms to ~3 ms.
- **Fusion:** Reciprocal Rank Fusion (`1/(K+rank)` summed across rankers). RRF is scale-free, so bm25 distances and cosine similarities combine without normalization. Symbol-name FTS hits are folded in as high-signal.

Brute-force cosine is correct and fast to ~hundreds of thousands of chunks; an ANN
index (hnswlib over the same stored vectors) is the documented upgrade path beyond
that.

---

## 9. Digest: multi-resolution, budgeted, never silently lossy

`digest/builder.py` assembles the Tier-1 model; `digest/serialize.py` renders it
within a token budget at three levels:

- **L0 (default):** architecture brief — language mix, module table (with mined purpose + internal centrality), most-depended-on files, entry points, mined **decisions** (ADRs/specs), heuristic **conventions** (test framework, typed-error count, type-hint %, async-heaviness), README excerpt, recent commits. ~4K tokens, always fits.
- **L1/L2:** per-file outlines (signatures, +methods at L2). Large at full repo scope, so the agent pairs them with a `path_glob` to scope to a module.

When outlines exceed the budget, the serializer **stops and emits a drill-down
pointer** listing the remaining files and the exact tool calls to reach them.
Truncation is explicit, never silent. Symbol materialization is capped to bound
memory on very large repos.

---

## 10. MCP server

`mcp/server.py` is a **stdlib-only JSON-RPC 2.0 stdio server** — no `mcp` SDK
dependency (works on any Python, including where the SDK lacks a wheel). It
implements `initialize`, `notifications/initialized`, `tools/list`, `tools/call`,
`ping`. stdout carries only JSON-RPC (one compact object per line); all
diagnostics go to stderr. Tool handlers never raise out of the loop — failures
become `isError` results. `reflens install` registers the server in OpenCode
(`~/.config/opencode/opencode.json`) and Claude Code (`~/.claude.json`) and writes
a usage block into the global `AGENTS.md` / `CLAUDE.md`.

---

## 11. Cross-cutting guarantees

- **Losslessness + `verify`:** reconstructs every file from blobs, re-checks SHA-256, confirms declared==indexed (completeness, with a live re-derive to catch parser drift), and reports extraction coverage %.
- **Concurrency:** atomic re-ingest (§5) + per-DB vector cache keyed by file signature.
- **Security:** repo names are slugified with a path-traversal guard; FTS queries are operator-sanitized; archive ingestion (Repomix) is content-only; the binary installers (RTK/etc.) are out of scope here but use safe extraction.
- **Local-first:** all state in `~/.reflens`; no network except optional model download (semantic) and explicit git-URL clone.

---

## 12. Decision log (chose X / rejected Y / because Z)

- **Two tiers, retrieval as safety net** — rejected "stuff it all in the window" because it's physically impossible at scale and fails silently. The honest model is navigable completeness + provable lossless store.
- **Zero core dependencies** — chose stdlib `sqlite3`/FTS5 + `ast` + hand-rolled MCP over the `mcp` SDK + a vector DB, because it installs anywhere, runs offline, and code search is largely lexical. Heavy capabilities (semantic, tree-sitter, accurate tokens) are opt-in extras.
- **Lexical default, semantic opt-in** — CPU embedding is ~25 chunks/s; making it the default would mean a 12-minute ingest. Lexical FTS5 is instant and strong for code; semantic is a worthwhile upgrade you choose.
- **`Repo` facade as the one seam** — so CLI and MCP can't diverge.
- **Atomic swap over in-place rebuild** — re-ingesting while serving must not corrupt or blank the index.
- **Brute-force vectors, ANN deferred** — correct and fast at target scale; not worth the index/persistence complexity until repos exceed ~200 MB.

---

## 13. Scalability register

- **H1 (a handful of reference repos, ≤ ~100 MB each):** the current design is correct. SQLite + gzip blobs + brute-force cosine + per-DB vector cache. No changes needed.
- **H2 (large repos / many chunks):** the single bottleneck is the brute-force cosine scan. Upgrade path: hnswlib ANN index over the already-stored float32 vectors (storage format is ready).
- **H3 (a shared, multi-user reference library):** the boundary to draw is the store — move SQLite + blobs behind a service with a shared cache; trigger only when concurrent multi-user access forces it. Not before.

---

## 14. Testing doctrine

Behavior over implementation, integration-biased. The suite (63 tests) covers the
load-bearing invariants: **lossless round-trip** (the crux), Repomix parsing
(including compressed-dump artifact stripping), Python/TS/markdown extraction,
hybrid search + FTS injection safety, digest budgeting + truncation pointer,
atomic re-ingest (queryable + no leftover dirs), vector-cache hit, symbol-body
read for point symbols, internal-centrality stdlib-collision guard, MCP protocol
(initialize/tools/list/tools/call), and completeness/coverage in `verify`.
