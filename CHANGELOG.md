# Changelog

All notable changes to reflens are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [0.3.0] — 2026-06-29

### Added
- **Incremental semantic re-ingest.** A re-ingest now reuses a symbol's prior
  embedding when its composed surface text is unchanged, so the expensive embed
  pass only runs over genuinely new/changed symbols. Measured on headroom
  (24,464 symbols): an unchanged re-ingest dropped from **~250 s to ~8 s (~30×)**
  (two runs: 255→7.6 s and 249.9→8.4 s). On by default;
  `ingest_source(..., reuse_embeddings=False)`
  forces a full embed. Inspired by content-keyed caching in the DeepSpec data
  pipeline (`jsonl_dataset` / `target_cache`).
- **Embedding pipeline fingerprint** (`embed_fingerprint`: resolved model + dim +
  pipeline version), stored per index. Reuse is refused unless the prior
  fingerprint matches exactly, so vectors are never mixed across models or across
  a change to `compose_symbol_text`. Bump `_EMBED_PIPELINE_VERSION` to force a
  clean re-embed when the embedding pipeline changes.
- `benchmark/perf_incremental.py`: reproducible full-vs-incremental measurement.

### Hardened (from an adversarial review of the reuse path)
- The prior index is opened `immutable=1` — genuinely read-only, writing no
  `-shm`/`-wal` sidecars into the live prior directory.
- Reuse-map loader degrades to a full embed (never crashes the ingest) on any
  malformed fingerprint meta, unreadable/incompatible prior index, or error; the
  file URI is properly encoded (paths with spaces/unicode no longer silently
  disable reuse); each candidate vector is length-validated against the model dim.
- A pre-0.3 index (no fingerprint) is treated as incompatible: the first
  re-ingest re-embeds once to establish the fingerprint, then reuse kicks in.

## [0.2.0] — 2026-06-29

### Added
- **Reproducible retrieval benchmark** (`benchmark/run.py`, `BENCHMARKS.md`):
  reflens hybrid search vs. the native-ripgrep baseline on 12 plain-English
  tasks over a real 1,750-file repo. Reports hit-rate@8, MRR, and tokens-to-
  answer — including the 2/12 cases reflens loses, with root cause.
- **Lossless fuzz/property tests** (`tests/test_lossless_fuzz.py`): random bytes
  across sizes (0–256 KB), pathological inputs (nulls, invalid UTF-8, BOMs),
  arbitrary unicode, content-addressing/idempotency, and corruption detection.
  Suite is now 74 tests, zero non-pytest test deps.

### Changed
- **Hybrid search now demotes test/spec files** for definition-style queries
  (`reflens/search/hybrid.py`). Test names read like the query and previously
  out-ranked implementations. The penalty preserves recall (tests still appear),
  is intent-aware (skipped when the query mentions "test"/"spec"), and is
  language-agnostic (path conventions across Python/Go/JS/Ruby/Rust). Measurably
  surfaced real implementations and improved MRR.

### Fixed
- Embedding-model mismatch between index build and query time now degrades to
  lexical retrieval instead of raising on the matmul; the build-time model is
  recorded in repo meta and reused at query time.

## [0.1.0] — initial public release

### Added
- **Two-tier engine.** Tier-1 budgeted intelligence digest (architecture brief,
  module map, mined decisions/conventions, centrality) + Tier-2 byte-exact,
  content-addressed blob store with `verify` (SHA-256 reconstruction).
- **Lossless ingest** from local directories, git URLs (auto-clone), and
  repomix `.md` dumps; streaming, with atomic-swap rebuilds.
- **Symbol extraction**: Python via `ast` (exact), markdown headings, regex
  fallback, optional tree-sitter (`[code]` extra) for more languages.
- **Internal-dependency graph** with centrality ranking (stdlib-name collision
  guard) powering "most-depended-on files".
- **Hybrid search**: FTS5 bm25 + symbol-name + opt-in symbol-surface semantic
  embeddings (`[semantic]` extra, fastembed/ONNX), fused with Reciprocal Rank
  Fusion. Symbol-level (not chunk-level) embeddings — faster and more accurate.
- **MCP stdio server** exposing 8 tools (`list`, `modules`, `map`, `search`,
  `read`, `neighbors`, `history`, `verify`) over a hand-rolled JSON-RPC loop;
  embedder prewarm; per-DB vector + signal caches keyed on file signature.
- **One-command host install** wiring OpenCode + Claude Code and writing
  AGENTS.md/CLAUDE.md usage guidance.
- **Zero required runtime dependencies** (stdlib `sqlite3`+FTS5, `ast`); extras:
  `[semantic]`, `[code]`, `[tokens]`.

[0.2.0]: https://github.com/cybertronayush/reflens/releases/tag/v0.2.0
[0.1.0]: https://github.com/cybertronayush/reflens/releases/tag/v0.1.0
