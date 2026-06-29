# Changelog

All notable changes to reflens are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

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
