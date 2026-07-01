# Eval: does the answer reach the agent's context window?

[`BENCHMARKS.md`](BENCHMARKS.md) shows reflens *ranks* the right file cheaply.
This eval asks the question the premise rests on: does the **answer-bearing file
actually reach the agent's context**, with reflens vs. the things people do
without an index (grep-and-read, paste-the-repo)?

## Correction: reflens is navigation-first, and I had been testing it wrong

reflens is designed to be used **map → drill → read**: read the architecture
digest (`reflens_map`), which names the modules and most-depended-on files;
drill into the relevant module to list its files; then `reflens_read` the one you
want. My earlier benchmarks called `search()` **in isolation** and skipped the
digest entirely — reflens's headline feature. That understated it by ~2×.

On the 12 headroom tasks, does the flow surface the answer file at all?

| usage | surfaces the answer file |
|---|--:|
| **reflens, as designed (map + module drill-down)** | **11/12 (91%)** |
| reflens, `search()` in isolation | 5/12 (41%) |

The six files search-only "missed" — `memory.py`, `semantic_cache.py`, `toin.py`,
`code_compressor.py`, `savings.py`, `wrap_rtk_metrics.py` — are **all named by the
module drill-down**. `code_compressor.py`, the flagship "loss" in
`BENCHMARKS.md`, is right there in the digest's most-depended-on list. Those were
**usage errors on my part, not reflens failures.** (The single remaining miss,
`ccr_regression_benchmark.py`, is a ground-truth artifact — a benchmark file, not
the real CCR implementation.)

**Lesson baked back into the product:** if the author defaults to search-only,
naive integrations will too. The `reflens_search` tool description now explicitly
routes broad "how/where" questions to `reflens_map` first.

## Budget check: was the answer file READ within budget? (search-only path)

All methods read file content into a fixed budget; the variable is which files,
in what order. This still uses the *weaker* search-only reflens path (an honest
lower bound), against the real alternatives:

| token budget | reflens (search) | grep-dump | full-dump |
|--:|--:|--:|--:|
| 1,500 | **5/12** | 0/12 | 0/12 |
| 3,000 | **5/12** | 0/12 | 0/12 |
| 6,000 | **5/12** | 0/12 | 0/12 |
| 12,000 | **6/12** | 0/12 | 0/12 |

Even the crippled search-only path beats the alternatives cleanly: grep-and-read
and paste-the-repo reach the answer file **~never** in a realistic budget — a
common keyword matches 1,000+ files, and reading them top-down never gets there.
Used as designed (11/12), the gap is wider still.

## What this does and doesn't prove

- **Does:** reflens gets the answer within reach where the no-index alternatives
  don't, and correct (navigation-first) usage roughly doubles the search-only
  result.
- **Doesn't:** final-answer correctness — reading/surfacing the file is necessary,
  not sufficient. The definitive eval (an LLM answers, graded) is still not done.
- It is author-built. Mitigations: strong realistic baselines, file-inclusion
  ground truth (not reflens's own ranking), and full disclosure — including that
  my *own* earlier numbers were measured with incorrect usage.

## Reproduce

```bash
reflens add /path/to/repo --name hr --semantic
python benchmark/context_sufficiency.py --repo hr
```
