# peak-research-v2 — Configuration

This file centralizes every reusable knob so the METHOD and tools stay generic.
Overriding these is the ONLY edit you should make for a new topic — never fork the logic.

## Paths
- CURATED_CATALOG: `D:\peak-search\curated_research_source_catalog.md`   (v3.0, 110 sources)
  NOTE: older copies live in `C:\Users\Martin\Downloads\...` — that path is WRONG. Use the D:\ one.
- OUTPUT_DIR: `D:\n8nchatbot-repo\research\<topic>.md`  (override per-run with `--output-dir`)
- CACHE_DIR: `<skill>/cache/`  (raw API responses; avoids re-spending OpenAlex credits)
- EVIDENCE_FILE: `<topic>_evidence.json`  (the working evidence table fed to S-Extract/Audit)

## API / identity
- OPENALEX_MAILTO: set this via env. The default `research@example.com` is a placeholder —
  the toolkit warns on startup when it is still in use, because a fake address drops you out
  of the OpenAlex polite pool and into anonymous rate limiting.
- OpenAlex is now a CREDIT/USD model: free budget resets at midnight UTC. Exhaustion -> 429 then 402.
  Mitigation: always `select=`; cache; prefer entity-GET over search.
- PEAK_CACHE_TTL_DAYS: cache entry lifetime, default 30. Set 0 to cache forever.

## Topic adaptation (no per-topic edits required)
Discovery queries, the relevance vocabulary, venue weighting and contradiction grouping terms
are all derived from `--topic` and then expanded from the retrieved corpus itself. There is no
topic keyword list to maintain. Tune behaviour through `relevance_threshold` (default 0.12)
and `top_k_fulltext` only.

## Fallback chain (when primary blocked) — empirically validated Aug 2026
```
Crossref (DOI verify) -> arXiv API (https+UA, retry) -> r.jina.ai -> raw curl
  -> GitHub API (UA; 60/hr; auth for code) -> Semantic Scholar (expect 429)
```
If ALL fail: flag honestly, drop source, do NOT fabricate. Firecrawl is usually 402 — treat as unavailable.

## Subagent contract (max 3 concurrent; leaf only)
- Each child: explicit goal + toolkit path + allowed source types + must return PRIMARY findings (title/abstract/citation) as JSON.
- Children use terminal + curl only (no execute_code, per parent policy).
- Children RECORD failures (e.g. "arxiv returned empty") — never substitute a source.
- Parent merges children's JSON into one EVIDENCE_FILE and runs `verify_records` (Audit gate).
- Never dispatch >3; never let a child ask the user questions.

## Quality gates (non-negotiable)
- G1 Retrieve: every source has stable ID + retrieval timestamp + raw capture. Records below
  the relevance threshold are kept as `screening: discovery_only` — recorded, never citable.
- G2 Extract: every candidate typed (fact/derived/inference/hypothesis/recommendation/untyped)
  + linked to >=1 source. `untyped` is reported as a share, not hidden.
- G3 Audit (`tools/retrieval.py verify EVIDENCE_FILE [--live]`): 0 unverified sources before
  publish. `--live` additionally re-resolves every DOI via Crossref and fails on title mismatch;
  without it the gate is structural and cannot detect a mis-attributed citation.
- G4 High-stakes (legal/medical/financial/safety): human review required before authority use.
  The `high_stakes_factual` archetype forces `--live-verify`.
- G5 Publish (`check_artifact`): required sections present as real headings, >=5 distinct
  source identifiers, no placeholder text, no empty sections. **Failing G5 blocks publication** —
  the run leaves `<topic>.draft.md` and exits non-zero.

## Topic archetypes (pick ONE; tunes depth/queries)
- literature_review: broad, cite-heavy, recency-weighted.
- architecture_decision: options + tradeoffs + recommendation, fewer but deeper sources.
- high_stakes_factual: maximal verification, human-review flag on.
