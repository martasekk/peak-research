---
name: peak-research
description: Evidence-first deep-research pipeline — one 5-phase METHOD (Plan → Retrieve → Extract → Synthesize → Audit) over a validated retrieval toolkit (OpenAlex, arXiv, Crossref, Jina, GitHub, Semantic Scholar), with a typed claim ledger, a mandatory contradiction pass, and publish gates that block a malformed or unverifiable artifact. Use for literature reviews, architecture and strategy decisions, competitive or market research, or any high-stakes question whose answer has to be sourced and auditable. Also use when asked to verify that citations resolve, to check an existing research document for structure, or to find papers on a topic.
---

# peak-research — evidence-first deep research

Produces a **citable, audit-gated research artifact**. One coherent METHOD, a CONFIG layer,
and a `run_research.py` orchestrator that enforces the phase order and the gates.

## What the script does and does not do

Read this before trusting an artifact it produces.

**It does:** run four retrieval passes plus citation/author/venue expansion; screen records
by topical relevance; pull fulltext where an OA copy exists; select assertive sentences as
**claim candidates**; flag candidates echoed by more than one source; look for
opposing-direction statements; and gate the output on source resolution and artifact
structure.

**It does not:** judge whether a claim is true, weigh study quality, or establish that a
corpus represents its literature.

**Two modes, and the artifact says which one produced it:**

| | Default (stdlib only) | `--adjudicate` (needs an LLM endpoint) |
|---|---|---|
| Claim typing | cue matching over surface wording — weak, and labelled as such | an LLM reads each sentence; non-substantive ones are dropped |
| Contradictions | lexical direction-of-effect → **triage candidates** | METHOD step 13 triage: genuine conflict vs method/definition/scope mismatch |
| Cost | free | per-run API spend; cached responses cost $0 |

Without `--adjudicate`, an empty contradiction section means *the heuristic found nothing*,
not that the literature agrees. With it, a verdict still carries its own confidence — the
model is judging an extracted fragment without the surrounding paper.

Either way the artifact is a **research starting point, not a finding set.** Open the source
before citing anything from it.

## How to run

1. Read `CONFIG.md` (paths, API identity, fallback chain, subagent contract, quality gates).
2. Read `METHOD.md` (the 5 phases / 20 steps — the actual procedure).
3. Retrieve ad hoc with the toolkit, or run the whole pipeline:

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/peak-research/tools/retrieval.py" openalex "short stem" 2023 5
```

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/peak-research/run_research.py" --topic "your question here" --archetype literature_review --live-verify
```

Outside a plugin install, run the same commands from this skill directory with plain
relative paths (`python run_research.py --topic "..."`).

4. Publishing is gated: G1 (retrieval health) → G3 (sources resolve) → G5 (artifact
   structure). A run that fails G5 leaves a `.draft.md` and does **not** write the
   published path.

The plugin also ships slash commands that wrap all of this: `/peak-research:research`,
`/peak-research:retrieve`, `/peak-research:verify`, `/peak-research:cost`,
`/peak-research:doctor`.

## Where things get written

Nothing is written inside the plugin. Every writable location resolves through
`tools/paths.py`, defaulting to the directory you invoke from:

| What | Default | Override |
|---|---|---|
| Run state (PLAN/EVIDENCE/AUDIT json) | `./.peak-research/runs/<topic>/` | `PEAK_WORKSPACE`, `--workspace` |
| API + LLM response cache | `./.peak-research/cache/` | `PEAK_CACHE_DIR` |
| Published deliverable | `./research/<topic>.md` | `PEAK_OUTPUT_DIR`, `--output-dir` |
| Curated source catalog | none | `PEAK_CATALOG` |

## Optional: LLM adjudication

`--adjudicate` replaces the two weakest heuristics with a model that reads the sentences.
Two backends:

**Anthropic** — adds one dependency; gets schema-valid JSON by construction.

```bash
pip install anthropic && export ANTHROPIC_API_KEY=...
```

**Any OpenAI-compatible endpoint** — NVIDIA NIM, OpenRouter, Together, Groq, vLLM, Ollama.
Uses stdlib `urllib`, so it adds **no dependency at all**.

```bash
export PEAK_ADJUDICATION_PROVIDER=nvidia
export NVIDIA_API_KEY=nvapi-...
# PEAK_ADJUDICATION_MODEL is optional — the benchmarked default chain applies when unset
```

Probe the wiring before spending anything: `/peak-research:doctor`, or
`python tools/adjudicate.py --check`.

`openai` here names the **wire protocol**, not the vendor — nothing goes to OpenAI unless
you point `PEAK_ADJUDICATION_BASE_URL` there. `nvidia`, `nim`, `nemotron`, `vllm`, `ollama`,
`openrouter`, `together`, `groq` are all accepted aliases for it.

| Variable | Purpose |
|---|---|
| `PEAK_ADJUDICATION_PROVIDER` | `anthropic` \| `nvidia`/`openai`/… (else inferred from which key is set) |
| `PEAK_ADJUDICATION_MODEL` | model id, or a **comma-separated chain** tried in order. Verify ids against the provider's catalog — a wrong id is the usual first failure |
| `PEAK_ADJUDICATION_MODEL_TYPING` | chain for claim typing only (bulk: 20 sentences per call) |
| `PEAK_ADJUDICATION_MODEL_CONTRADICTIONS` | chain for contradiction triage only (6 clusters per call) |
| `PEAK_ADJUDICATION_BASE_URL` | defaults per key (NVIDIA → `integrate.api.nvidia.com/v1`) |
| `PEAK_ADJUDICATION_API_KEY` | explicit key; otherwise `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, … |
| `PEAK_ADJUDICATION_AUTH_HEADER` / `_AUTH_PREFIX` | for gateways that don't take `Authorization: Bearer` |
| `PEAK_ADJUDICATION_EXTRA_HEADERS` | JSON object of extra headers |
| `PEAK_ADJUDICATION_USD_PER_CALL` | per-call cost estimate for the log (default 0.02) |

**Structured output differs by backend.** Anthropic guarantees schema-valid JSON.
OpenAI-compatible servers vary, so that path degrades `json_schema` → `json_object` → no
hint, strips reasoning traces and markdown fences, extracts the outermost JSON object, and
drops malformed or out-of-vocabulary rows rather than failing the batch. A dropped row just
keeps its heuristic value.

**Model chains and fallback.** `PEAK_ADJUDICATION_MODEL` accepts a comma-separated chain.
A retired model (404), a rate limit (429), a server error or a truncated response steps to
the next entry; only a credential failure (401/403) aborts, since no other model behind the
same key would fare better. The default NVIDIA chain is
`nemotron-3-super-120b-a12b` → `nemotron-3-ultra-550b-a55b`, ordered by measurement rather
than size — see `docs/MODEL_BENCHMARK.md`, where Super beat Ultra on speed *and* accuracy on
both tasks, and Ultra misclassified genuine empirical conflicts as method mismatches.

The two tasks can take different chains via `PEAK_ADJUDICATION_MODEL_TYPING` and
`PEAK_ADJUDICATION_MODEL_CONTRADICTIONS`, since typing is bulk classification and triage is
comparative reasoning over few items.

Any failure — missing SDK or key, rate limit, HTTP error, refusal, truncation, unparseable
output — degrades to the heuristics after the chain is exhausted. It never breaks a run.
Responses cache by provider+model+prompt; the cache is checked for **every** model in the
chain before any network call, so a corpus previously adjudicated by the second model is
served from disk instead of re-failing the first. Re-running a corpus costs $0.

## Retrieval toolkit

`tools/retrieval.py` — OpenAlex (search/DOI/by-id, `select=` cost control), arXiv (https+UA),
Jina reader, GitHub, Crossref, Semantic Scholar, `verify_records` (G3) and `check_artifact` (G5).
Hash-keyed disk cache with a 30-day TTL (`PEAK_CACHE_TTL_DAYS`).
Patterns + catalog: `references/retrieval_catalog.md`. Principles: `references/methodology_principles.md`.

For agent-driven retrieval rather than the scripted pipeline, dispatch the bundled
`peak-retriever` subagent — it carries the same contract (leaf only, max 3 concurrent,
record failures, never substitute a source).

## Hard rules

- Every material claim gets a citation that supports the wording.
- Typed claim ledger: fact / derived / inference / hypothesis / recommendation / **untyped**.
  `untyped` is a real outcome — do not launder it into `derived`.
- Contradiction pass mandatory; genuine disagreement ≠ method/definition mismatch.
  If the automated pass returns nothing, the pass is **incomplete**, not clean.
- Catalog rule: original > abstract > aggregator.
- High-stakes (legal/medical/financial/safety): human review before authority use.
  The `high_stakes_factual` archetype turns on `--live-verify` automatically.
- Do NOT fabricate sources/DOIs/quotes — drop or flag if unretrievable.
- Fallback chain when primary blocked: Crossref → arXiv → Jina → raw curl → GitHub → Semantic Scholar.
  If ALL fail: flag honestly. Firecrawl is usually 402 — treat as unavailable.

## Output

Deliverable goes to the output dir (see the table above): brief, subquestions,
evidence matrix, claim ledger, contradiction analysis, gaps, synthesis, conclusions, sources, audit.

## Tests

- `python test_gate.py` — both gates and every regression in `docs/CHANGELOG.md`.
- `python test_adjudicate.py` — the LLM layer, run against a stub client.

Neither needs the network, credentials, or the `anthropic` package.
