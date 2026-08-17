# peak-research — Methodology Principles (VALIDATED AGAINST REAL SOURCES)

> Compiled Aug 2026 from 5 sources read live via r.jina.ai. These principles SHORE UP the
> existing 20-stage playbook (they are not a replacement). Each maps to one or more stages.
> Sources read (real): Self-RAG (2310.11511), MindSearch (2407.20183), GAIA (2311.12983),
> Anthropic "Building Effective Agents", gpt-researcher README. One attempted source
> (2502.01163) returned an unrelated math paper and was DROPPED — not cited.
> Unverified: production failure-rate figures for the specific modes (principles 3-6 are
> synthesized from the architectures, not quoted from a single source).

## The 12 principles
1. **Decompose into atomic sub-questions as a dynamic graph** (MindSearch) — nodes are
   independently answerable; let the graph grow from results, not a fixed plan. → S3 subquestions.
2. **Adaptive, on-demand retrieval, not fixed-k** (Self-RAG) — retrieve only when a gap is
   detected; avoid noise from indiscriminate passage dumps. → S7/S8 retrieval strategy.
3. **Four-pass retrieval: discovery → targeted → contradiction → gap** — broad scan, then
   precise sourcing, then seek dissent, then fill uncovered sub-claims. → S6/S7/S8/S14.
4. **Source-quality hierarchy: original primary > derivative abstract > aggregator** — cite the
   dataset/paper/preprint, not a blog summarizing it. → S10 quality; catalog Rule #6.
5. **Contradiction triage: genuine empirical disagreement ≠ method/definition mismatch** —
   reconcile by checking population, metric, and timeframe before calling it a conflict. → S13.
6. **Typed claim ledger: fact / derived / inference / recommendation** — every claim tagged and
   traceable to a source; inferences flagged as non-factual. → S12 (already in stage).
7. **Citation auditing: verify every cited URL resolves and says what you claim** — kill
   hallucinated/phantom citations before synthesis. → S19 (enforced by `tools/retrieval.py verify`).
8. **No snippet-as-evidence** — a search snippet is a lead, not a source; fetch and read the
   passage in context. → S8 (enforced by toolkit: snippets rejected, full fetch required).
9. **Programmatic gates between stages** (Anthropic chaining) — assert sub-answer quality/
   coverage before advancing; abort or re-plan on failure. → S9 screening, S19 audit gate.
10. **Parallelize independent sub-tracks; use voting for confidence** (Anthropic) — sectioning
    for speed, multiple retrievers for robustness against noise. → subagent contract (max 3).
11. **Separate planner/synthesizer from retriever** (orchestrator-workers, gpt-researcher) —
    division of labor bounds context drift and token overflow. → S2 plan vs S16 synthesize vs S8 retrieve.
12. **Prefer the simplest sufficient architecture** (Anthropic) — add agentic loops only when a
    single RAG call can't meet the bar. → S1 scope guard.

## How these map to the shipped toolkit
- P3 (four-pass) → `openalex_search` (discovery) + `resolve_paper` (targeted) + you supply
  contradiction/gap passes as additional queries; all funnel into `verify_records` (S19).
- P7 (citation audit) + P8 (no snippet) → `verify_records` rejects any record without a resolved
  title+abstract; the toolkit never returns snippets as evidence.
- P9 (programmatic gates) → `run_demo.py` asserts `res["pass"]` before "publishing".
- P10 (parallelize) → the subagent contract in SKILL.md supports up to 10 concurrent leaf workers.

## Honesty notes (do not strip)
- These principles are guidance, not measured benchmarks. The subagent could NOT pull production
  failure-rate numbers; treat principles 3-6 as design heuristics, not empirical laws.
- If you later want measured numbers (e.g., "adaptive retrieval reduces hallucination by X%"),
  that requires a fresh literature pass on evaluation benchmarks (FRAMES, SimpleQA, GAIA,
  BrowseComp) — out of scope for this hardening pass.
