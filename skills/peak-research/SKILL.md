---
name: peak-research
description: Evidence-first deep-research pipeline — one 5-phase METHOD (Plan → Retrieve → Extract → Synthesize → Audit) over a validated retrieval toolkit (OpenAlex, arXiv, Crossref, Jina, GitHub, Semantic Scholar), with a typed claim ledger, a mandatory contradiction pass, and publish gates that block a malformed or unverifiable artifact. Use for literature reviews, architecture and strategy decisions, competitive or market research, or any high-stakes question whose answer has to be sourced and auditable. Also use when asked to verify that citations resolve, to check an existing research document for structure, or to find papers on a topic.
---

# peak-research — evidence-first deep research

Produces a **citable, audit-gated research artifact**. One coherent METHOD, a CONFIG layer,
and an orchestrator that enforces the phase order, subagent dispatch, and quality gates.

## MANDATORY SUBAGENT INVOCATION DIRECTIVE

When this skill is executed by an AI Agent (Antigravity / Gemini / Claude):

> **HARD REQUIREMENT**: The agent **MUST** actively use subagents (`invoke_subagent` with `peak-retriever` or `research` role, up to 10 concurrent) for Phase 2 parallel retrieval passes and research tracks. Do **NOT** execute all retrieval sequentially in a single main context when subagents are available.

### Subagent Execution Architecture:
1. **Phase 1 (Plan)**: Main agent derives objective, subquestions, and query stems (`PLAN.json`).
2. **Phase 2 (Retrieve - Concurrent Leaf Subagents)**: Main agent spawns up to 10 parallel subagents using `invoke_subagent`:
   - **Worker 1 (`peak-retriever`)**: Discovery pass (broad literature and core stems).
   - **Worker 2 (`peak-retriever`)**: Contradiction pass (actively seeking dissent, failure replications, critiques).
   - **Worker 3 (`peak-retriever`)**: Gap & targeted pass (answering specific subquestions and boundary conditions).
3. **Phase 3 (Extract & Claim Typing)**: Native model adjudication or subagent typing into `Fact`, `Derived`, `Inference`, `Hypothesis`, `Recommendation`.
4. **Phase 4 (Synthesize & Contradiction Triage)**: Triage opposing statements (genuine conflict vs methodology/scope mismatch).
5. **Phase 5 (Audit & Gating)**: Strict verification (G1 retrieval health -> G3 citation resolution -> G5 artifact structure).

---

## What the pipeline does and does not do

**It does:** run four retrieval passes plus citation/author/venue expansion; screen records
by topical relevance; pull fulltext where an OA copy exists; select assertive sentences as
**claim candidates**; flag candidates echoed by more than one source; look for
opposing-direction statements; and gate the output on source resolution and artifact
structure.

**It does not:** judge whether a claim is true, weigh study quality, or establish that a
corpus represents its literature.

**Two modes:**

| | Default (Native / Stdlib) | `--adjudicate` (External LLM API) |
|---|---|---|
| Claim typing | Native Agent Subagents or cue matching | LLM API reads each sentence; non-substantive ones dropped |
| Contradictions | Native Model Triage or lexical direction | LLM API triage: genuine conflict vs mismatch |
| Cost | Free / Platform native | Per-run API spend; cached responses cost $0 |

## How to run

1. Read `CONFIG.md` (paths, API identity, fallback chain, subagent contract, quality gates).
2. Read `METHOD.md` (the 5 phases / 20 steps — the actual procedure).
3. Execute ad-hoc retrieval with the toolkit or run the pipeline orchestrator:

```bash
python "${extensionPath}/skills/peak-research/tools/retrieval.py" openalex "short stem" 2023 5
```

```bash
python "${extensionPath}/skills/peak-research/run_research.py" --topic "your question here" --archetype literature_review --live-verify
```

Relative path from the skill directory:
```bash
python run_research.py --topic "your question here" --archetype literature_review --live-verify
```

4. Publishing is gated: G1 (retrieval health) -> G3 (sources resolve) -> G5 (artifact
   structure). A run that fails G5 leaves a `.draft.md` and does **not** write the
   published path.

## Where things get written

Every writable location resolves through `tools/paths.py`, defaulting to the working directory:

| What | Default | Override |
|---|---|---|
| Run state (PLAN/EVIDENCE/AUDIT json) | `./.peak-research/runs/<topic>/` | `PEAK_WORKSPACE`, `--workspace` |
| API + LLM response cache | `./.peak-research/cache/` | `PEAK_CACHE_DIR` |
| Published deliverable | `./research/<topic>.md` | `PEAK_OUTPUT_DIR`, `--output-dir` |
| Curated source catalog | none | `PEAK_CATALOG` |

## Optional: LLM adjudication

`--adjudicate` replaces heuristics with model evaluation.
- **NVIDIA NIM / OpenAI-compatible**:
  ```bash
  export PEAK_ADJUDICATION_PROVIDER=nvidia
  export NVIDIA_API_KEY=nvapi-...
  ```
- **Anthropic**:
  ```bash
  pip install anthropic && export ANTHROPIC_API_KEY=...
  ```

Run diagnostics:
```bash
python "${extensionPath}/skills/peak-research/test_gate.py"
python "${extensionPath}/skills/peak-research/test_adjudicate.py"
```
