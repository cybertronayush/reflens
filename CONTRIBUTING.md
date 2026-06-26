# Contributing to reflens

Thanks for your interest. reflens is a small, dependency-light codebase — easy to
hack on.

## Setup

```bash
git clone https://github.com/cybertronayush/reflens && cd reflens
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"        # add ,semantic,code for the optional backends
.venv/bin/python -m pytest -q
```

The core has **zero runtime dependencies** (stdlib `sqlite3`/FTS5, `ast`, a
hand-rolled MCP stdio server). Keep it that way — anything heavy (embeddings,
tree-sitter, tokenizers) belongs behind an optional extra and must degrade
gracefully when absent.

## Ground rules

- **Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** The two-tier model and the
  module contracts are the things to preserve.
- **Losslessness is sacred.** Never change a code path such that
  `reflens verify <repo>` could fail on a directory ingest. Add a test if you
  touch the store or ingest.
- **Test behavior, not implementation.** Add/extend tests under `tests/`; the
  suite is the contract. `pytest -q` must stay green.
- **Match the conventions** already in the codebase (typing, error handling,
  graceful degradation when an optional dep is missing).
- **Small, focused PRs.** One change per PR; describe the tradeoff if it isn't
  obvious.

## Where things live

| Area | Module |
|---|---|
| Lossless store (blobs + SQLite) | `reflens/store/` |
| Ingest (dir / git URL / repomix) | `reflens/ingest/` |
| Symbol/heading extraction | `reflens/extract/` |
| Dependency graph + centrality | `reflens/graph/` |
| Search (lexical + semantic) | `reflens/search/` |
| Tier-1 digest | `reflens/digest/` |
| `Repo` facade | `reflens/engine.py` |
| MCP server | `reflens/mcp/server.py` |
| CLI + host install | `reflens/cli/` |

## Reporting issues

Include: what you ran (`reflens add … / map … / search …`), the repo kind
(dir / git URL / repomix), the actual vs expected output, and the platform.
If you can, attach the output of `reflens verify <repo>`.

By contributing you agree your contributions are licensed under Apache-2.0.
