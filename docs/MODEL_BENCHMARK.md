# Adjudication model benchmark — NVIDIA nemotron-3 family

Measured 2026-08-17 against `integrate.api.nvidia.com/v1`, through the plugin's own
`type_claims()` and `adjudicate_contradictions()` — so every number includes the full
path: auth, `response_format` degradation, reasoning-trace stripping, JSON extraction,
row coercion.

The headline: **`super-120b-a12b` beat `ultra-550b-a55b` on both tasks, on both speed
and quality.** The default chain is ordered by this result, not by parameter count.

## Model ids

All three resolve; verified against `GET /v1/models` (102 models, 7 in the nemotron-3
family). The first id configured — `nvidia/llama-3.1-nemotron-ultra-253b-v1`, inherited
from the pre-plugin docs — returns 404 and has been removed.

## Task 1 — claim typing

One real 20-claim batch (`CLAIMS_PER_CALL = 20`) drawn from a live run on "retrieval
augmented generation hallucination". All 20 were `untyped` under the lexical heuristic,
so this measures exactly what adjudication is bought to fix.

| model | wall | per claim | rescued from `untyped` | high-confidence rows |
|---|---|---|---|---|
| **super-120b-a12b** | **57.3s** | 2.9s | 16/20 | **16** |
| ultra-550b-a55b | 84.7s | 4.2s | 16/20 | 8 |
| lightning-30b-a3b | **timed out** (540s) | — | — | — |

Lightning was retested at smaller batches rather than written off on one timeout; it
completes but costs 20–40× more per claim. See its section below.

Both usable models rescued the same 16 rows, but they disagreed on *what* those rows
are. Ultra collapsed almost everything into one bucket — 13 `derived`, 0 `fact` — while
Super produced a spread (5 `fact`, 6 `derived`, 3 `inference`, 2 `recommendation`) and
was twice as likely to commit to high confidence. A typed ledger where 80% of rows say
`derived` is barely more informative than one that says `untyped`.

## Task 2 — contradiction triage

Six constructed cases with a defensible expected verdict each: two genuine empirical
conflicts, two method mismatches, one scope mismatch, one non-conflict. Constructed
because the live run surfaced zero candidates, so there was no real ground truth to
score against.

| model | wall | score |
|---|---|---|
| **super-120b-a12b** | **22.0s** | **5/6** |
| ultra-550b-a55b | 39.3s | 3/6 |

Ultra's two extra misses are the ones that matter: it labelled **both genuine
empirical conflicts** as `method-mismatch` — "reduced hallucination 34%" vs "increased
hallucination 12%", same benchmark, and an identical opposite-direction pair on chunk
size. A model that reclassifies real disagreement as a definitional artefact silently
defeats the pass METHOD.md calls mandatory. That is a worse failure than missing a
contradiction outright, because the artifact then reports the triage as *done*.

Both models missed the same sixth case (`not-a-conflict` → `scope-mismatch`). That one
is arguably a scoring disagreement rather than a model error — dense-vs-BM25 on
different task types is genuinely a scope distinction — so read the scores as 5/6 and
3/6 at worst, 6/6 and 4/6 at best. The gap between them holds either way.

## lightning-30b-a3b

Despite the name, it is by far the slowest of the three, and the gap is not close.
Tested at three batch sizes because a single timeout is not enough to write a model off:

| batch | wall | per claim | rescued | confidence |
|---|---|---|---|---|
| 2 (probe) | 72s | 36s | — | — |
| 3 | 342.7s | **114.2s** | 0/3 | all low |
| 8 | 462.9s | **57.9s** | 6/8 | mixed |
| 20 | **timed out** (540s = 3 `response_format` modes × the 180s read timeout) | — | — | — |

Super runs 2.9s/claim. Lightning is **20–40× slower per claim** and never becomes
competitive: shrinking the batch makes the per-claim cost *worse*, not better, which is
the signature of a large fixed reasoning preamble per request rather than slow decoding.
The A3B active-parameter count predicts fast tokens, so the wall time is trace length.

Extrapolated to the 102-claim corpus used above: Super finishes in ~6 minutes
(6 batches × 57s). Lightning in 8-claim batches would need ~100 minutes (13 × 463s) —
and 20-claim batches, the configured size, do not complete at all.

Quality is no consolation either. At n=3 it rescued **0 of 3** claims and returned low
confidence on every row; the n=8 run did better (6/8) but on samples this small that is
one data point, not a trend. The latency finding is the unambiguous one.

It is **not** in the default chain, and it is not a usable fallback for any task in this
pipeline — both jobs here are batched structured-output classification, the workload it
handles worst. If you want it anyway, set it explicitly and expect a run measured in
hours.

## Reproducing

```bash
python tools/adjudicate.py --check          # one probe call per configured model
```

The two benchmark scripts are not shipped — they are throwaway harnesses that import
`type_claims` / `adjudicate_contradictions` directly, pin `PEAK_ADJUDICATION_MODEL` per
iteration, and time the call. Cache is keyed by model, so each model makes a genuine
call; re-running the same model afterwards is free and measures nothing.

## What this does not establish

Two tasks, one corpus, one batch each, one run per model — no repeats, so the timings
carry unmeasured variance and could be affected by server load at the time. The
contradiction cases are hand-written by one author and scored against that author's
expected verdicts. Treat the ordering as well-founded and the exact numbers as
indicative. Re-run before assuming they still hold after a model revision.
