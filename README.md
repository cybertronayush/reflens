# reflens

**Give an AI coding agent the full context of a large reference repository — without losing any of it.**

You point reflens at a repo (a local directory, a git checkout, or a [Repomix](https://github.com/yamadashy/repomix) `.md` dump — even 100 MB+). It builds a **two-tier** representation and serves it to OpenCode / Claude Code (or any MCP host) as tools the agent calls.

---

## The honest premise

You **cannot** fit a large repo (a 100 MB repo is ~25M tokens) into a 200K-token context window losslessly. Any tool that claims it can is silently truncating — and that silent truncation is the bug that makes agents hallucinate about code they "have."

reflens refuses to lie about this. Instead it gives you **two tiers**, and a way to *prove* nothing was lost:

| Tier | What it is | Loss |
|---|---|---|
| **1 — Intelligence Digest** | Dense, in-context-budgeted overview: architecture, language mix, directory tree, entry points, mined intent (README/commits), and the **full public symbol surface** (every signature + docstring + line anchor) | Lossy on *implementation bodies*, **complete on structure & meaning** |
| **2 — Lossless Store** | Every byte of every file, content-addressed (gzip + SHA-256) | **Zero** — `reflens verify` reconstructs every file and checks SHA-256 |

The agent **reasons from Tier 1** and **expands into Tier 2** (exact source) only when a task needs the gnarly detail. Retrieval is the safety layer, not the primary mechanism — exactly as it should be.

Where the digest truncates to fit a budget, it emits a **drill-down pointer** telling the agent the exact tool call to reach the rest. Nothing becomes unreachable.

---

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
# optional extras:
#   .venv/bin/pip install -e ".[semantic]"   # vector search (fastembed, ONNX)
#   .venv/bin/pip install -e ".[code]"        # multi-language AST (tree-sitter)
#   .venv/bin/pip install -e ".[tokens]"      # accurate token budgeting (tiktoken)
```

Core has **zero runtime dependencies** — stdlib `sqlite3` (FTS5), `ast`, and a hand-rolled MCP stdio server. It works fully offline; your reference code never leaves the machine.

---

## Use

```bash
# 1. Ingest a reference repo (dir, git repo, or repomix .md)
reflens add /path/to/flagship-repo --name flagship
reflens add ./repomix-output-some-repo.md          # a Repomix dump
reflens add /path/to/repo --name flagship --semantic   # + vector search

# 2. Prove nothing was lost
reflens verify flagship          # SHA-256 round-trip over every file

# 3. Explore the way the agent will
reflens map flagship --level 2                    # the whole-repo digest
reflens map flagship --glob 'src/**' --level 2    # zoom into a subtree
reflens search flagship "retry backoff"           # hybrid search
reflens read flagship src/core.py --start 40 --end 80   # byte-exact
reflens read flagship MyClass                     # symbol body
reflens neighbors flagship src/core.py            # imports / imported-by

# 4. Wire it into your agent
reflens install both     # registers the MCP server with OpenCode + Claude Code
```

Restart OpenCode / Claude Code, then tell it: *"Use `reflens_list`, then `reflens_map` the `flagship` repo and apply its retry pattern to my project."*

---

## MCP tools the agent gets

| Tool | Purpose |
|---|---|
| `reflens_list` | what reference repos are available |
| `reflens_modules(repo)` | compact table-of-contents (modules + internal-dependency weight) — start here |
| `reflens_map(repo, level?, path_glob?, budget_tokens?)` | the Tier-1 digest: architecture brief (modules, internal centrality, decisions, conventions) + outlines; zoom with `path_glob` |
| `reflens_search(repo, query, k?, mode?)` | hybrid lexical(FTS5)+semantic search → ranked `file:line` hits |
| `reflens_read(repo, target, start?, end?)` | **byte-exact** source by file path or symbol name |
| `reflens_neighbors(repo, target, limit?)` | dependency expansion (imports / imported-by / defines) |
| `reflens_history(repo, target?, limit?)` | git history (repo-wide or per file) from the live source |
| `reflens_verify(repo)` | prove losslessness + completeness + extraction coverage |

### Navigation model (how "the whole repo" fits a window)

A large repo's full signature surface can exceed a context window (headroom's is ~370K tokens). So the digest is **hierarchical**: `reflens_map(repo, level=0)` is a small always-fits **architecture brief** (modules + most-depended-on files + decisions + conventions); the agent then drills per module with `reflens_map(repo, path_glob="<module>/**", level=2)`. Where any view truncates, it prints the exact tool call to reach the rest — so nothing is unreachable.

### Semantic search & LLM enrichment (opt-in)

- **Semantic search**: `reflens add <src> --semantic` builds vector embeddings (fastembed/ONNX) for concept queries lexical can't match. It's opt-in because CPU embedding is slow (~25 chunks/sec). Lexical FTS5 is the instant default.
- **LLM enrichment**: `reflens enrich <repo>` (needs `OPENAI_API_KEY` or `--api-key`) writes per-module prose summaries (intent/patterns) into the digest — the one path to "reason as if direct access" pre-loaded rather than retrieved. Off by default; provider-agnostic (any OpenAI-compatible endpoint).

---

## How it works

```
source (dir / git / repomix.md)
  │  stream file-by-file (100MB+ stays flat in memory)
  ▼
┌──────────────────────────────────────────────────────────────┐
│ Tier 2 (lossless)         Tier 1 (intelligence)               │
│ ─────────────────         ────────────────────                │
│ content-addressed         AST outline (py=stdlib ast exact;   │
│ gzip blobs + SHA-256      others=tree-sitter→regex)           │
│ SQLite: files/chunks      symbols, imports → dependency graph │
│                           FTS5 + optional vectors             │
└──────────────────────────────────────────────────────────────┘
  │  MCP stdio (stdlib JSON-RPC, no SDK dep)
  ▼
OpenCode / Claude Code  → map · search · read · neighbors · verify
```

- **Ingest** streams each file: hash → gzip blob (Tier 2) → outline + chunk + index (Tier 1). Memory stays flat regardless of repo size.
- **Outlines**: Python is exact (stdlib `ast`); other languages use tree-sitter when the optional pack is installed, else a regex outliner — so it always produces *something* with zero optional deps.
- **Search**: SQLite FTS5 (lexical) always; fastembed vectors when `--semantic`; fused with Reciprocal Rank Fusion (scale-free, no fragile normalization).
- **Storage**: `~/.reflens/repos/<name>/` (override with `REFLENS_HOME`).

---

## Losslessness caveat (read this)

- **Directory / git ingest** is lossless with respect to the **true source** — `verify` confirms byte-identity.
- **Repomix ingest** is lossless with respect to the **dump**. If the dump was generated with `--remove-comments`, `--output-show-line-numbers`, or `--compress`, that processing is baked in. reflens detects and **warns** about this. For byte-identical-to-source fidelity, ingest the directory.

---

## License

Apache-2.0.
