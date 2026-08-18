# peak-research — a Claude Code plugin

Evidence-first deep research that produces a **citable, audit-gated artifact** — and refuses
to publish one that fails its own gates.

Five phases (Plan → Retrieve → Extract → Synthesize → Audit), a validated stdlib-only
retrieval toolkit over OpenAlex / arXiv / Crossref / Jina / GitHub / Semantic Scholar, a typed
claim ledger, a mandatory contradiction pass, and publish gates G1–G5.

## Install

```bash
/plugin marketplace add <owner>/peak-research-plugin
```

```bash
/plugin install peak-research@peak-research
```

From a local checkout, point the marketplace at the directory instead:

```bash
/plugin marketplace add /path/to/peak-research-plugin
```

Requires Python 3.9+. No pip installs — the toolkit is stdlib only. The optional LLM
adjudication layer works over any OpenAI-compatible endpoint with no dependency, or over the
`anthropic` SDK if you install it.

## Use

| Command | Does |
|---|---|
| `/peak-research:research <question>` | the full gated pipeline, end to end |
| `/peak-research:retrieve <topic>` | ad-hoc literature search, no artifact |
| `/peak-research:verify <path>` | G3 (sources resolve) or G5 (artifact structure) |
| `/peak-research:cost` | cumulative API spend for this workspace |
| `/peak-research:doctor` | tests, path resolution, API identity, LLM wiring |

The `peak-research` skill also loads on its own whenever a task calls for sourced research, so
you can just ask. The `peak-retriever` agent is available for parallel sub-track retrieval —
at most 3 concurrent, leaf only.

Or drive the pipeline directly:

```bash
python skills/peak-research/run_research.py --topic "your question" --archetype literature_review --live-verify
```

## Configure

Set `OPENALEX_MAILTO` to a real address before anything else — the placeholder drops you out
of the OpenAlex polite pool into anonymous rate limiting.

Nothing is written inside the plugin install. Every writable location resolves relative to the
directory you invoke from:

| | Default | Override |
|---|---|---|
| Run state | `./.peak-research/runs/<topic>/` | `PEAK_WORKSPACE`, `--workspace` |
| Response cache | `./.peak-research/cache/` | `PEAK_CACHE_DIR` |
| Published artifact | `./research/<topic>.md` | `PEAK_OUTPUT_DIR`, `--output-dir` |
| Curated source catalog | none | `PEAK_CATALOG` |

Add `.peak-research/` to your project's `.gitignore`.

Full knob list — cache TTL, adjudication provider/model/base-URL, gate thresholds, archetypes
— is in [`skills/peak-research/CONFIG.md`](skills/peak-research/CONFIG.md).

## What it does and does not do

**It does:** four retrieval passes plus citation/author/venue expansion; relevance screening;
fulltext pull where an OA copy exists; assertive-sentence selection as claim candidates;
cross-source echo detection; opposing-direction search; and gating on source resolution and
artifact structure.

**It does not:** judge whether a claim is true, weigh study quality, or establish that a
corpus represents its literature.

G3 proves a citation resolves to the work recorded. G5 proves the artifact has the required
shape. **A well-formed, wrong artifact passes both.** Without `--adjudicate`, an empty
contradiction section means the heuristic found nothing — not that the literature agrees.

The output is a research starting point, not a finding set. Open the source before citing
anything from it.

## Optional: LLM adjudication

`--adjudicate` swaps the two weakest heuristics — cue-based claim typing, lexical
contradiction detection — for a model that reads the sentences.

```bash
# Anthropic: schema-valid JSON by construction, one dependency
pip install anthropic && export ANTHROPIC_API_KEY=...
```

```bash
# Any OpenAI-compatible endpoint: NVIDIA NIM, OpenRouter, Together, Groq, vLLM, Ollama.
# stdlib urllib, no dependency at all. The model chain has a benchmarked default.
export PEAK_ADJUDICATION_PROVIDER=nvidia
export NVIDIA_API_KEY=nvapi-...
```

`PEAK_ADJUDICATION_MODEL` takes a comma-separated **chain**, tried in order: a retired
model (404), a rate limit (429), or a server error steps to the next; a credential failure
(401/403) aborts, since no other model behind that key would do better. Claim typing and
contradiction triage can take separate chains via `PEAK_ADJUDICATION_MODEL_TYPING` and
`PEAK_ADJUDICATION_MODEL_CONTRADICTIONS`.

The NVIDIA default is `nemotron-3-super-120b-a12b` → `nemotron-3-ultra-550b-a55b`, ordered
by measurement, not parameter count — see [docs/MODEL_BENCHMARK.md](docs/MODEL_BENCHMARK.md).

`openai` names the **wire protocol**, not the vendor — nothing reaches OpenAI unless you point
`PEAK_ADJUDICATION_BASE_URL` there. Probe the wiring with `/peak-research:doctor` before
spending. Any failure — missing key, rate limit, refusal, truncation, unparseable output —
degrades to the heuristics and never breaks a run. Responses cache by provider+model+prompt,
so re-running a corpus costs $0.

## Layout

```
.claude-plugin/     plugin.json, marketplace.json
commands/           the five slash commands
agents/             peak-retriever — leaf retrieval worker
skills/peak-research/
  SKILL.md          entry point
  METHOD.md         the 5 phases / 20 steps
  CONFIG.md         every knob
  run_research.py   the orchestrator
  tools/            paths, retrieval, adjudicate, subagent worker
  references/       validated API patterns, methodology principles
  test_gate.py      gates + every regression in docs/CHANGELOG.md
  test_adjudicate.py  the LLM layer, against a stub client
docs/               CHANGELOG, COMPARISON, and the pre-hardening baseline
```

## Tests

```bash
cd skills/peak-research && python test_gate.py && python test_adjudicate.py
```

Neither needs network, credentials, or the `anthropic` package.
