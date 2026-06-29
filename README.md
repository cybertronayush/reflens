<div align="center">

# reflens

**Give your AI coding agent the full, lossless context of any reference repository — and let it reason over a codebase far larger than its context window.**

Point it at a local folder, a GitHub URL, or a [Repomix](https://github.com/yamadashy/repomix) dump. reflens indexes it once and serves it to OpenCode / Claude Code (or any MCP host) as tools your agent calls on its own.

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-63%20passing-brightgreen.svg)](tests/)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple.svg)](https://modelcontextprotocol.io)
[![Zero core deps](https://img.shields.io/badge/core%20deps-0-brightgreen.svg)](pyproject.toml)

</div>

---

## The problem

You want your coding agent to **learn from a flagship repo and apply its patterns to your project**. But the repo is 100k–1M+ tokens — it does not fit in the context window. Today you either:

- paste fragments and hope they're the right ones, or
- clone it into your workspace and let the agent blind-`grep` it every session (slow, token-hungry, no orientation), or
- dump it with Repomix and watch it overflow the window.

All three silently lose context. **Silent truncation is the bug** — the agent confidently reasons about code it never actually saw.

## The approach: two tiers (the honest part)

You **cannot** fit a 25M-token repo into a 200K window losslessly — that's physics, not engineering. Any tool that claims otherwise is truncating behind your back. reflens refuses to, and gives you two tiers plus a way to *prove* nothing was lost:

| Tier | What it is | Loss |
|---|---|---|
| **1 — Intelligence Digest** | A budgeted, in-context overview: architecture (modules + most-depended-on files), entry points, mined conventions & decisions, and the **full public symbol surface** (every signature + docstring + line anchor) | Lossy on *bodies*, **complete on structure & meaning** |
| **2 — Lossless Store** | Every byte of every file, content-addressed (gzip + SHA-256) | **Zero** — `reflens verify` reconstructs every file and checks SHA-256 |

The agent **reasons from Tier 1** and **expands into Tier 2** (exact source) only when a task needs the gnarly detail. Retrieval is the safety net, not the primary mechanism. Where the digest hits a budget, it prints the exact tool call to reach the rest — so nothing becomes unreachable.

→ Full design in [ARCHITECTURE.md](ARCHITECTURE.md).

## Quickstart (60 seconds)

```bash
# 1. Install (isolated; pipx recommended)
pipx install "git+https://github.com/cybertronayush/reflens"

# 2. Wire it into your agent (edits OpenCode + Claude Code configs, adds usage guidance)
reflens install both

# 3. Stock the library — any local dir, GitHub URL, or repomix .md
reflens add https://github.com/tiangolo/fastapi --name fastapi
reflens add /path/to/your/reference-repo --name myref
reflens add ./repomix-output.md --name dump

# 4. Prove nothing was lost (directory ingests are byte-exact)
reflens verify myref

# 5. Restart OpenCode / Claude Code, then just ask:
#    "Use reflens to learn fastapi's dependency-injection pattern and apply it to my app."
```

That's it. The agent calls the tools automatically — no slash-commands, no per-repo setup.

## How your agent uses it

Once installed, **every model in every project** gets the `reflens_*` tools (global MCP server) plus a usage note in your global `AGENTS.md` / `CLAUDE.md`. A typical agent flow:

```
reflens_modules(repo)              → the module map (table of contents)
  → reflens_map(repo)              → architecture brief (hubs, conventions, decisions)
  → reflens_map(repo, path_glob)   → zoom into a module at signature detail
  → reflens_search(repo, query)    → find the relevant code (hybrid lexical+semantic)
  → reflens_read(repo, target)     → byte-exact source of a file or symbol
  → reflens_neighbors / _history   → dependencies / git history
```

You never write commands for the agent. For 100% reliability on a given task, just name the repo (*"learn from the `fastapi` repo…"*).

## MCP tools

| Tool | Purpose |
|---|---|
| `reflens_list` | which reference repos are indexed |
| `reflens_modules(repo)` | compact table-of-contents (modules + internal-dependency weight) |
| `reflens_map(repo, level?, path_glob?, budget_tokens?)` | Tier-1 digest: architecture brief (default, ~4K tokens) → per-module outlines (`path_glob` + `level=2`) |
| `reflens_search(repo, query, k?, mode?)` | hybrid lexical (FTS5) + semantic search → ranked `file:line` hits |
| `reflens_read(repo, target, start?, end?)` | **byte-exact** source by file path or symbol name |
| `reflens_neighbors(repo, target, limit?)` | dependency expansion (imports / imported-by / defines) |
| `reflens_history(repo, target?, limit?)` | git history (repo-wide or per file) |
| `reflens_verify(repo)` | prove losslessness + completeness + extraction coverage |

## CLI reference

```bash
reflens add <source> --name <n> [--semantic] [--max-file-bytes N] [--include-binary]
reflens list
reflens modules <name>                          # table of contents
reflens map <name> [--level 0|1|2] [--glob 'src/**'] [--budget N]
reflens search <name> "<query>" [-k N] [--mode auto|lexical|semantic|hybrid]
reflens read <name> <path|symbol> [--start N --end N]
reflens neighbors <name> <path|symbol>
reflens history <name> [path]
reflens verify <name>                            # SHA-256 round-trip + completeness + coverage
reflens enrich <name> [--model ...]              # optional LLM per-module summaries
reflens remove <name> -y
reflens install [opencode|claude|both]           # wire the MCP server + agent guidance
reflens serve                                    # the MCP stdio server (hosts launch this)
```

## What gets extracted

- **Python** → exact, via the stdlib `ast` (classes, methods, functions, constants, module docstrings, imports).
- **TypeScript / JavaScript / Go / Rust / Java / Kotlin / C / C++ / C# / Ruby / PHP / Swift / Scala / Shell** → signature outlines via a tuned regex extractor (optionally `tree-sitter` with `pip install 'reflens[code]'`).
- **Markdown** → heading outline (docs/specs/ADRs become navigable).
- **Everything else** → stored losslessly + full-text searchable.

## Losslessness — and proof

```bash
$ reflens verify myref
{ "ok": true,
  "files": 1750, "verified": 1750, "failed": [],
  "completeness": { "declared_files": 1750, "indexed_files": 1750, "drift_detected": false },
  "extraction": { "code_files": 1287, "with_symbols": 1235, "coverage_pct": 96.0 } }
```

Every stored file is content-addressed and re-hashed on read; `verify` reconstructs all of them and compares SHA-256. **Directory/git ingests are byte-identical to source.** Repomix `--compress` dumps are lossless *with respect to the dump* (their bodies were already stripped) — reflens detects and warns about this; ingest the directory for true source fidelity.

The byte-exact store is also fuzz-tested: `tests/test_lossless_fuzz.py` throws random bytes (every size from 0 to 256 KB), pathological inputs (nulls, invalid UTF-8, BOMs), and arbitrary unicode at the blob layer and asserts exact round-trips, content-addressing, and corruption detection (a blob that decompresses to the wrong bytes fails its SHA check instead of returning garbage).

## Proof: retrieval benchmark (incl. where it loses)

[**`BENCHMARKS.md`**](BENCHMARKS.md) is a reproducible harness (`benchmark/run.py`) comparing reflens's hybrid search against the baseline an agent actually uses — ripgrep the query's content words over the cloned source — on 12 plain-English retrieval tasks over a real 1,750-file Python+Rust repo:

| metric | reflens | native grep |
|---|--:|--:|
| hit-rate @8 | **10/12 (83%)** | 12/12 |
| MRR | **0.46** | — |
| mean tokens to read to get the answer | **108** | ~4,200,000 |

Both surface the target; the difference is *usability*. reflens puts it in the top 8 at ~100 tokens; grep's content-word union over a large repo returns 500–1,300 files because words like *content*, *code*, and *cache* appear nearly everywhere — an answer vs. a haystack. **reflens also loses 2/12** (paraphrastic/acronym queries whose words don't overlap the implementation's symbol surface — e.g. a query saying "strip comments" against code that calls itself "AST-based syntax-preserving compression"). Both losses are documented with root cause and the known fix (query expansion). Building the benchmark also surfaced and fixed a real bug: tests out-ranked implementations on "where is X" queries, so reflens now demotes (never drops) test files — skipped when the query is itself about tests.

## Semantic search (opt-in)

Lexical FTS5 is the instant default and is excellent for code (symbol names, error strings). For concept queries ("how do they handle retries?"), build embeddings:

```bash
pipx install "reflens[semantic] @ git+https://github.com/cybertronayush/reflens"
reflens add /path/to/repo --name myref --semantic
```

Embeddings use `fastembed` (ONNX, no torch) and index the **symbol surface** (signature + docstring), not raw code bodies — so a concept query like "detect content type and pick a compressor" returns the actual function, not a doc page. It's opt-in (a one-time ~4 min build for a large repo); the vector matrix is cached in-process so repeat queries are ~3 ms. Lexical FTS still covers full file content, and byte-exact retrieval is unchanged.

**Re-ingest is incremental.** A symbol's embedding is reused when its surface text is unchanged, so re-indexing a repo you've already built only embeds what actually changed — an unchanged re-ingest of a 24k-symbol repo drops from **~250 s to ~8 s (~30×)**. Reuse is gated on an exact pipeline fingerprint (model + dim + version), so vectors are never mixed across models or composition changes. See [`CHANGELOG`](CHANGELOG.md) and `benchmark/perf_incremental.py`.

## Compared to

| | reflens | clone + agent `grep` | Repomix / gitingest | editor codebase index |
|---|---|---|---|---|
| External reference repos as a persistent library | ✅ | ✗ (in your tree) | ✗ (one file) | ✗ (your repo) |
| Architecture-first orientation | ✅ | ✗ | ✗ | partial |
| Bigger-than-window handling | ✅ navigable | ✗ overflows / re-explores | ✗ overflows | ✅ |
| Lossless + provable | ✅ `verify` | n/a | partial | n/a |
| One install across hosts (MCP) | ✅ | n/a | n/a | per-editor |

reflens is for **"I want my agent to learn from N flagship repos I don't want cluttering my workspace."** For a single repo you're actively editing, your editor's built-in tools are fine.

## Honest limitations

- **It's navigable, not omniscient.** The agent must query well; the architecture-first design + AGENTS.md guidance steer it, but a lazy agent still gets shallow context. Name the repo for reliability.
- **Semantic ingest is slow** (CPU embeddings). Lexical-only is instant and the default.
- **`reflens_history` needs a live git source dir** — unavailable for URL-cloned or repomix repos (the digest still shows recent commit subjects).
- **Retrieval misses paraphrastic/acronym queries** whose words don't overlap the code's symbol surface or body (measured: 2/12 in [`BENCHMARKS.md`](BENCHMARKS.md)). Query expansion is the planned fix. For an exact token you already know, plain `grep` is equal and simpler — reflens wins on concept queries over large repos, not on everything.
- **The grep token-cost ratio scales with repo size** — huge on a 1,750-file repo, negligible on a 50-file one. On tiny repos, just grep.

## Install & setup

reflens has **zero required runtime dependencies** — it runs on the standard
library alone (`sqlite3` with FTS5, `ast`, and a hand-rolled MCP stdio server).
The **core** works on **Python 3.10+**. The optional extras (`semantic`, `code`)
pull `fastembed`/`tree-sitter`, whose wheels can lag the newest Python, so for
those use **Python 3.12** (recommended).

Pick one install path, then do the same three post-install steps.

### Path A — pipx (isolated, simplest)

```bash
pipx install "reflens[semantic] @ git+https://github.com/cybertronayush/reflens"
# core only: pipx install "git+https://github.com/cybertronayush/reflens"
```

### Path B — from source (this is exactly how the reference setup runs)

```bash
git clone https://github.com/cybertronayush/reflens && cd reflens
python3.12 -m venv .venv
.venv/bin/pip install -e ".[semantic,code,tokens]"

# make the `reflens` command available globally (PATH must include ~/.local/bin)
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/reflens" ~/.local/bin/reflens
```

### Then (any path) — wire it in, restart, stock the library

```bash
reflens install both        # registers the MCP server in OpenCode + Claude Code
                            # and writes a usage block into their global AGENTS.md / CLAUDE.md
# → restart OpenCode / Claude Code so they launch the server
reflens add <dir|git-url|repomix.md> --name myref   # populate the library (repeat per repo)
reflens list                # confirm
```

`reflens install` wires each host to launch the server via the interpreter that
has reflens installed, e.g.:

```json
// ~/.config/opencode/opencode.json  →  mcp.reflens
{ "type": "local",
  "command": ["/abs/path/.venv/bin/python", "-m", "reflens", "serve"],
  "enabled": true }
// ~/.claude.json  →  mcpServers.reflens  (command/args form, same interpreter)
```

Local state lives in `~/.reflens` (override with `REFLENS_HOME`). Nothing leaves
your machine. To update reflens itself, `git pull` (source) or re-run the pipx
install, then restart your agent.

## Contributing

```bash
git clone https://github.com/cybertronayush/reflens && cd reflens
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## License

[Apache-2.0](LICENSE).
