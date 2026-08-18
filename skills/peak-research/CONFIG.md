# peak-research — Configuration

This file centralizes every reusable knob so the METHOD and tools stay generic.
Overriding these is the ONLY edit you should make for a new topic — never fork the logic.

## Paths

Nothing is written inside the plugin. The install directory is shared and is replaced
wholesale on update, so every writable location resolves through `tools/paths.py`,
relative to the directory you invoke from.

| Setting | Env var | Default | Also settable by |
|---|---|---|---|
| Workspace root | `PEAK_WORKSPACE` | `<cwd>/.peak-research` | `--workspace` |
| Run state | — | `<workspace>/runs/<topic-slug>/` | via workspace |
| Response cache | `PEAK_CACHE_DIR` | `<workspace>/cache/` | via workspace |
| Published deliverable | `PEAK_OUTPUT_DIR` | `<cwd>/research/<topic>.md` | `--output-dir` |
| Curated source catalog | `PEAK_CATALOG` | unset (optional) | — |

- **Run state** holds `PLAN.json`, `EVIDENCE.json`, `FULLTEXTS.json`, `CLAIM_LEDGER.json`,
  `EVIDENCE_MATRIX.json`, `SYNTHESIS.json`, `AUDIT.json`, `G5_CHECK.json`,
  `RETRIEVAL_FAILURES.json`. `EVIDENCE.json` is the evidence table fed to Extract and Audit.
- **Cache** holds raw API responses and adjudication responses. Deleting it costs money on
  the next run and nothing else. Add `.peak-research/` to a project's `.gitignore`.
- **Catalog** is optional. The METHOD only consults it when choosing between sources, so its
  absence degrades source selection rather than breaking a run. Point `PEAK_CATALOG` at your
  own curated list if you keep one.

## API / identity

- `OPENALEX_MAILTO`: set this. The default `research@example.com` is a placeholder — the
  toolkit warns on startup while it is still in use, because a fake address drops you out of
  the OpenAlex polite pool and into anonymous rate limiting.
- OpenAlex runs a CREDIT/USD model: the free budget resets at midnight UTC. Exhaustion gives
  429, then 402. Mitigation: always `select=`; cache; prefer entity-GET over search.
- `PEAK_CACHE_TTL_DAYS`: cache entry lifetime, default 30. Set 0 to cache forever.

## LLM adjudication (optional, `--adjudicate`)

Off by default; the pipeline runs on lexical heuristics without it. Full setup and the
provider table are in `SKILL.md`. The model knobs:

| Env var | Purpose |
|---|---|
| `PEAK_ADJUDICATION_MODEL` | comma-separated **chain**, tried in order |
| `PEAK_ADJUDICATION_MODEL_TYPING` | chain for claim typing only (20 sentences/call) |
| `PEAK_ADJUDICATION_MODEL_CONTRADICTIONS` | chain for contradiction triage only (6 clusters/call) |

Fallback rules: 404 (model retired), 429 (rate limit), 5xx, timeout, truncation and
unparseable output all step to the next model in the chain. 401/403 aborts — no other model
behind the same key would succeed. When the chain is exhausted the run keeps its heuristics
and says so. The cache is probed for every model in the chain before any network call.

NVIDIA default: `nemotron-3-super-120b-a12b` → `nemotron-3-ultra-550b-a55b`. That order is
measured, not assumed — Super was faster *and* more accurate on both tasks, and Ultra
downgraded genuine empirical conflicts to method mismatches. See `docs/MODEL_BENCHMARK.md`.

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

If ALL fail: flag honestly, drop the source, do NOT fabricate. Firecrawl is usually 402 —
treat as unavailable.

## Subagent contract (max 3 concurrent; leaf only)

Applies both to the scripted workers `run_research.py` spawns (`tools/subagent_retrieve.py`)
and to the `peak-retriever` agent this plugin ships.

- Each child: explicit goal + toolkit path + allowed source types + must return PRIMARY
  findings (title/abstract/citation) as JSON.
- Children use the toolkit and terminal only.
- Children RECORD failures (e.g. "arxiv returned empty") — never substitute a source.
- Parent merges children's JSON into one evidence file and runs `verify_records` (Audit gate).
- Never dispatch >3; never let a child ask the user questions.

## Quality gates (non-negotiable)

- **G1 Retrieve** — every source has a stable ID + retrieval timestamp + raw capture. Records
  below the relevance threshold are kept as `screening: discovery_only` — recorded, never
  citable. The run aborts on >34% failed passes, a missing discovery pass, or <5 records.
- **G2 Extract** — every candidate typed (fact/derived/inference/hypothesis/recommendation/
  untyped) and linked to ≥1 source. `untyped` is reported as a share, not hidden.
- **G3 Audit** (`python tools/retrieval.py verify <EVIDENCE.json> [--live]`) — 0 unverified
  sources before publish. `--live` additionally re-resolves every DOI via Crossref and fails
  on title mismatch; without it the gate is structural and cannot detect a mis-attributed
  citation.
- **G4 High-stakes** (legal/medical/financial/safety) — human review required before
  authority use. The `high_stakes_factual` archetype forces `--live-verify`.
- **G5 Publish** (`python tools/retrieval.py check_artifact <final.md>`) — required sections
  present as real headings, ≥5 distinct source identifiers, no placeholder text, no empty
  sections. **Failing G5 blocks publication** — the run leaves `<topic>.draft.md` and exits
  non-zero.

## Topic archetypes (pick ONE; tunes depth/queries)

- `literature_review` — broad, cite-heavy, recency-weighted.
- `architecture_decision` — options + tradeoffs + recommendation, fewer but deeper sources.
- `high_stakes_factual` — maximal verification, human-review flag on, `--live-verify` forced.
