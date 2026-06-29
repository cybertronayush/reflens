# Eval: does the answer reach the agent's context window?

[`BENCHMARKS.md`](BENCHMARKS.md) shows reflens *ranks* the right file cheaply.
This eval asks the question the premise actually rests on: at a **fixed token
budget** (the slice of context an agent gives to repo knowledge), does the
**answer-bearing file get read into context at all** — with reflens vs. the two
things people do without an index?

> **Honest status: this is a directional check, not a knockout.** It supports the
> premise but does not prove final-answer correctness, and I reshaped the
> methodology four times getting here (disclosed in full below) — read it with
> that caveat. The cleaner primary proof is still the retrieval benchmark.

## Method

All three methods read code into a budget; the only variable is **which files,
in what order** (the real agent loop is "find files, then read them"):

- **reflens** — diversified hybrid search; reads each hit's **line-extent**
  (a ranged read — reflens's actual capability), best-first.
- **grep-dump** — files matching the query's content words, in ripgrep order,
  read **whole** (grep has no symbol structure).
- **full-dump** — files in path order, read whole ("paste the repo top-down").

Ground truth: the canonical non-test source file that answers each task.
**Grounded = that exact file was read within the budget** — tracked by file
inclusion, no symbol heuristics or string-matching. Tasks reuse the 12 from the
retrieval benchmark, over the 1,750-file headroom repo.

## Results

| token budget | reflens | grep-dump | full-dump |
|--:|--:|--:|--:|
| 1,500 | **5/12 (41%)** | 0/12 | 0/12 |
| 3,000 | **5/12 (41%)** | 0/12 | 0/12 |
| 6,000 | **5/12 (41%)** | 0/12 | 0/12 |
| 12,000 | **6/12 (50%)** | 0/12 | 0/12 |

**Read this honestly, both directions:**
- **The win is qualitative.** grep-dump and full-dump read the answer file
  **~never** in a realistic budget — a common keyword matches 1,000+ files, and
  reading them top-down never reaches the answer. reflens reads the answer file's
  slice instead. That gap (≥5 vs 0) is the whole point of an index.
- **reflens is not reliably good in absolute terms.** ~50% means for half the
  tasks reflens surfaced a *different* (often still-relevant) file than the one
  canonical file the ground truth fixed — and it includes the same 2/12
  paraphrase misses from the retrieval benchmark. "Much better than the
  alternatives" is not "always right."

## Disclosure: four methodology iterations

I changed the method four times. Hiding that would be the dishonest move, so:

1. **reflens = search snippets; grounded = file's first symbol appears.** → 25%
   flat. *Flaw:* snippets are signatures, and "first symbol" rarely equals the
   ranked/relevant one — measuring the wrong thing, underselling reflens.
2. **reflens reads whole files in rank order; grounded = first symbol.** → tied
   grep at small budgets. *Flaw:* "first symbol" ground truth is noisy.
3. **Grounded = answer file READ (no symbol heuristic); reflens reads whole
   files.** → reflens 4/12 @12k, grep/dump 0. *Flaw:* whole-file reads undersell
   reflens, which does **ranged** reads.
4. **reflens reads symbol line-extents (its real capability); grep/dump read
   whole files.** → the table above. This models each tool's actual behavior.

The risk in iterating is p-hacking — reshaping until the tool wins. Iterations
1→3 were genuine flaw-fixes; iteration 4 corrects a real modeling error (reflens
reads slices, grep reads files). I stopped there rather than tune `λ`, `k`,
budgets, or ground-truth strictness until the number looked better.

## What this does NOT show

- **Final-answer correctness.** Reading the answer file is necessary, not
  sufficient — an LLM still has to use it. The definitive eval (an agent answers,
  graded against ground truth) is **not done**; it needs a real grading loop.
- It is **author-built**. Mitigations: strong realistic baselines (grep, paste-
  the-repo), file-inclusion ground truth (not reflens's own ranking), all four
  iterations disclosed, losses reported. Re-run it on your repo before trusting it.

## Reproduce

```bash
reflens add /path/to/repo --name hr --semantic
python benchmark/context_sufficiency.py --repo hr
```
