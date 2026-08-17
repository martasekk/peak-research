# peak-research-v2 — Configuration

This file centralizes every reusable knob so the METHOD and tools stay generic.
Overriding these is the ONLY edit you should make for a new topic — never fork the logic.

## Paths
- CURATED_CATALOG: `D:\peak-search\curated_research_source_catalog.md`   (v3.0, 110 sources)
  NOTE: older copies live in `C:\Users\Martin\Downloads\...` — that path is WRONG. Use the D:\ one.
- OUTPUT_DIR: `D:\n8nchatbot-repo\research\<topic>.md`  (the .md deliverable)
- CACHE_DIR: `<skill>/cache/`  (raw API responses; avoids re-spending OpenAlex credits)
- EVIDENCE_FILE: `<topic>_evidence.json`  (the working evidence table fed to S-Extract/Audit)

## API / identity
- OPENALEX_MAILTO: `research@example.com`   (set via env OPENALEX_MAILTO to override)
- OpenAlex is now a CREDIT/USD model: free budget resets at midnight UTC. Exhaustion -> 429 then 402.
  Mitigation: always `select=`; cache; prefer entity-GET over search.

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
- G1 Retrieve: every source has stable ID + retrieval timestamp + raw capture.
- G2 Extract: every claim typed (fact/derived/inference/recommendation) + linked to >=1 source.
- G3 Audit (`tools/retrieval.py verify EVIDENCE_FILE`): 0 unverified sources before publish.
- G4 High-stakes (legal/medical/financial/safety): human review required before authority use.

## Topic archetypes (pick ONE; tunes depth/queries)
- literature_review: broad, cite-heavy, recency-weighted.
- architecture_decision: options + tradeoffs + recommendation, fewer but deeper sources.
- high_stakes_factual: maximal verification, human-review flag on.
