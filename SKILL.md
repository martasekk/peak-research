---
name: peak-research-v2
description: Evidence-first deep-research pipeline structured as a single 5-phase METHOD (Plan→Retrieve→Extract→Synthesize→Audit) with a validated retrieval toolkit and a config layer. Use for literature reviews, architecture/strategy decisions, or high-stakes sourced questions. Loads METHOD.md + CONFIG.md + tools/retrieval.py.
---

# peak-research-v2 — evidence-first deep research

A pipeline that produces a **citable, audit-gated research artifact**. Restructured from v1
(`peak-research`): one coherent METHOD instead of 20 fragmented stage files, a CONFIG layer,
and a working `run_research.py` orchestrator.

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

| | Default (stdlib only) | `--adjudicate` (needs the `anthropic` SDK) |
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
3. Retrieve with the toolkit: `python tools/retrieval.py openalex "short stem" 2023 5`, etc.
   Or run the full pipeline:

```bash
python run_research.py --topic "your question here" --archetype literature_review --live-verify
```

4. Publishing is gated: G1 (retrieval health) → G3 (sources resolve) → G5 (artifact
   structure). A run that fails G5 leaves a `.draft.md` and does **not** write the
   published path.

### Optional: LLM adjudication

`--adjudicate` replaces the two weakest heuristics with a model that reads the sentences.
Two backends:

**Anthropic** — adds one dependency; gets schema-valid JSON by construction.

```bash
pip install anthropic && export ANTHROPIC_API_KEY=...
python run_research.py --topic "..." --adjudicate
```

**Any OpenAI-compatible endpoint** — NVIDIA NIM, OpenRouter, Together, Groq, vLLM, Ollama.
Uses stdlib `urllib`, so it adds **no dependency at all**.

```bash
export PEAK_ADJUDICATION_PROVIDER=nvidia
export NVIDIA_API_KEY=nvapi-...
export PEAK_ADJUDICATION_MODEL=nvidia/llama-3.1-nemotron-ultra-253b-v1
python tools/adjudicate.py --check          # one probe call before spending
python run_research.py --topic "..." --adjudicate
```

`openai` here names the **wire protocol**, not the vendor — nothing goes to OpenAI unless
you point `PEAK_ADJUDICATION_BASE_URL` there. `nvidia`, `nim`, `nemotron`, `vllm`, `ollama`,
`openrouter`, `together`, `groq` are all accepted aliases for it.

| Variable | Purpose |
|---|---|
| `PEAK_ADJUDICATION_PROVIDER` | `anthropic` \| `nvidia`/`openai`/… (else inferred from which key is set) |
| `PEAK_ADJUDICATION_MODEL` | model id — **verify it against the provider's catalog; a wrong id is the usual first failure** |
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

Any failure — missing SDK or key, rate limit, HTTP error, refusal, truncation, unparseable
output — degrades to the heuristics. It never breaks a run. Responses cache by
provider+model+prompt, so re-running a corpus costs $0.

## Retrieval toolkit
`tools/retrieval.py` — OpenAlex (search/DOI/by-id, `select=` cost control), arXiv (https+UA),
Jina reader, GitHub, Crossref, Semantic Scholar, `verify_records` (G3) and `check_artifact` (G5).
Hash-keyed disk cache with a 30-day TTL (`PEAK_CACHE_TTL_DAYS`).
Patterns + catalog path: `references/retrieval_catalog.md`. Principles: `references/methodology_principles.md`.

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
Deliverable goes to `CONFIG.output_dir` (override with `--output-dir`): brief, subquestions,
evidence matrix, claim ledger, contradiction analysis, gaps, synthesis, conclusions, sources, audit.

## Tests
- `python test_gate.py` — 47 assertions: both gates and every regression in `CHANGELOG.md`.
- `python test_adjudicate.py` — 45 assertions for the LLM layer, run against a stub client.

Neither needs the network, credentials, or the `anthropic` package.

## Relationship to v1
v1 (`../peak-research/`) is the 20-separate-stage-file version, kept for per-step reference;
METHOD.md cross-links to it. See `COMPARISON.md`.
