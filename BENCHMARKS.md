# Benchmarks

Reproducible retrieval benchmark for reflens, with the method, the real numbers,
and the cases where reflens **loses**. A benchmark that only shows wins isn't
evidence — it's marketing. The harness is `benchmark/run.py`; run it yourself.

## What this measures (and what it doesn't)

This measures the **retrieval layer**: given a natural-language question about an
unfamiliar codebase, does the approach surface the right code, and how many
tokens must the agent read to get it into context?

It does **not** measure end-to-end agent task success — that needs a live agent
loop and is inherently noisy. Retrieval quality is the part reflens is
responsible for, so that's what we isolate and measure.

### The two approaches

- **reflens** — `reflens_search` (hybrid: FTS5 bm25 + symbol-name + symbol-surface
  semantic, fused with Reciprocal Rank Fusion), top 8 hits.
- **native grep** — the baseline an agent actually reaches for first: ripgrep the
  query's content words over the cloned source
  (`rg -li -e word1 -e word2 … -g '!**/node_modules/**'`). This is the honest
  "I don't have a code index" baseline.

### The queries

12 questions phrased in **plain English**, so the words deliberately differ from
the code's identifiers — the realistic "I don't know the symbol name yet" case.
Each has a known target file verified to exist in the source. Queries and
targets are hard-coded in `benchmark/run.py` (no cherry-picking per run).

### Metrics

| metric | reflens | native grep |
|---|---|---|
| **found@8** | target in top-8 ranked hits | target file anywhere in the ripgrep match set |
| **rank / MRR** | rank of first correct hit; MRR = mean of 1/rank | — (grep is unordered) |
| **tokens to answer** | snippets of hits 1..rank (chars/4) | bytes of **all** matched files /4 |

The grep token cost is the **worst case**: to be *certain* the answer is in the
match set, you must read every match. A real agent refines its grep iteratively
instead — but each refinement is another round-trip, and the results stay
file-level, not symbol-level. The number is directional, not a claim that an
agent literally reads 4M tokens.

## Test bed

- **Repo:** [headroom](https://github.com/chopratejas/headroom) — a real hybrid
  Python + Rust codebase. **1,750 files, 24,464 indexed symbols.**
- **Machine:** local, Apple Silicon, Python 3.12.
- **Embedding model:** `BAAI/bge-small-en-v1.5` via fastembed (the `[semantic]` extra).

## Results

| # | natural-language query | target | reflens (rank, tokens) | grep (files, tokens) |
|--:|---|---|---|---|
| 1 | how is the number of tokens in a piece of text counted | `tokeniz` | ✅ #4, 56 | ✅ 1,225f, 4.6M |
| 2 | detect what kind of content a string is and pick the right compressor | `content_router` | ✅ #6, 139 | ✅ 1,256f, 4.8M |
| 3 | store the original content so compression can be reversed and retrieved later | `ccr` | ✅ #4, 170 | ✅ 1,265f, 4.8M |
| 4 | compress a large JSON array of similar objects | `smart_crusher` | ✅ #5, 95 | ✅ 1,245f, 4.8M |
| 5 | parse server sent events from a streaming http response | `streaming` | ✅ #5, 257 | ✅ 1,118f, 4.6M |
| 6 | wrap a coding agent from the command line | `wrap` | ✅ #1, 11 | ✅ 1,151f, 4.4M |
| 7 | keep a vector store of memories with embeddings | `memory` | ✅ #1, 6 | ✅ 528f, 2.9M |
| 8 | stabilize the prompt prefix so the provider cache hits | `cache` | ✅ #1, 216 | ✅ 1,041f, 4.3M |
| 9 | decide compression aggressiveness from observed tool-output patterns | `toin` | ❌ | ✅ 1,300f, 4.7M |
| 10 | download and install the rtk binary from github releases | `rtk` | ✅ #1, 76 | ✅ 584f, 2.8M |
| 11 | strip comments and shrink code while keeping signatures | `code_compressor` | ❌ | ✅ 959f, 4.1M |
| 12 | track how many tokens were saved and the cost | `savings` | ✅ #2, 35 | ✅ 924f, 4.0M |

### Aggregate

| metric | reflens | native grep |
|---|--:|--:|
| hit-rate @8 | **10/12 (83%)** | 12/12 (100%) |
| MRR | **0.46** | — |
| mean tokens to answer | **108** | ~4,240,000 |

**The headline is not "reflens finds things grep can't."** Both surface the
target. The difference is *usability*: reflens puts it in the top 8 at ~108
tokens; grep's content-word union over a 1,750-file repo returns 500–1,300 files
(millions of tokens) because common words like *content*, *code*, *cache*, and
*compress* appear nearly everywhere. One is an answer; the other is a haystack.

## Where reflens loses — and why (read this part)

grep "found" all 12 targets; reflens missed 2. Both misses share **one cause**,
and it's the honest boundary of symbol-surface semantic retrieval:

- **#11 `code_compressor`** — the file describes itself as *"code-aware compressor
  using AST parsing for syntax-preserving compression."* The query says *"strip
  comments / shrink / signatures."* Zero vocabulary overlap with the symbol names
  **or** docstrings, and the words don't co-occur in the body either. Semantic
  search over symbol surfaces can't bridge that gap.
- **#9 `toin`** — *TOIN* is an internal acronym. A paraphrase like *"decide
  compression aggressiveness from tool-output patterns"* has nothing to latch
  onto. (Notably, an earlier version of reflens scored this as a "hit" — but the
  hit was a **test file** named `test_…_toin_gate.py`, not the implementation.
  Demoting tests — see below — correctly turned a hollow win into an honest miss.)

**The fix is known and not yet built:** query expansion / an acronym + synonym
layer before retrieval. That's logged as future work, not hidden. grep's
advantage here is also hollow — it "found" `code_compressor.py` only by matching
the common word *code* inside a 4.1M-token match set.

## A finding the benchmark forced: demote tests, don't drop them

Building this surfaced a real retrieval bug. Test files describe behavior in
plain English — their names and docstrings *read like the query* — so they
out-competed implementations on "where is X" questions. reflens now applies a
small, **intent-aware** rank penalty to test/spec paths (`hybrid.py`):

- recall is preserved — tests still appear and are still findable;
- an implementation that ties on relevance now ranks **above** a test;
- the penalty is **skipped** when the query is itself about tests
  (contains "test"/"spec"), so "where are the tests for X" still works.

This mirrors how Sourcegraph, GitHub code search, and ctags-based tools treat
tests for definition lookups. Effect on this benchmark: surfaced the real
`content_router` implementation (#2) and improved MRR, while honestly converting
the `toin` test-file false-positive into a miss.

## Reproduce

```bash
# 1. install with the semantic extra
pip install -e '.[semantic]'

# 2. index any repo you have locally (or a git URL)
reflens add /path/to/headroom --name hr --semantic

# 3. run the benchmark (targets are headroom-specific; edit TASKS for other repos)
python benchmark/run.py --repo hr -o BENCHMARKS_RUN.md
```

Numbers will vary with the repo, the embedding model, and ripgrep's gitignore
handling. The harness prints per-task rows so you can audit every win and loss.

## Honesty notes

- **This benchmark was written by reflens's author to evaluate reflens.** That is
  a conflict of interest. Mitigations: a strong real-world baseline (ripgrep, not
  a strawman), targets verified to exist before scoring, plain-English queries
  fixed in source, and full disclosure of both losses with root causes. Re-run it
  on your own repo before trusting the ratio.
- The **token-efficiency ratio depends on repo size** — it grows with the repo
  (more files match a common word) and shrinks on tiny repos where grep is
  already fine. On a 50-file project, just grep.
- reflens's win is **paraphrastic / concept queries on large repos**. For an exact
  token you already know (`grep "def dispatch_compressor"`), grep is equal and
  simpler. reflens doesn't replace grep; it covers the case grep is worst at.
