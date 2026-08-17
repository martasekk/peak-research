# peak-research — Evidence-First Deep Research (Native Subagent Enabled)

peak-research produces a **citable, audit-gated research artifact** using a 5-phase methodology (Plan -> Retrieve -> Extract -> Synthesize -> Audit), an automated Python orchestrator (`run_research.py`), a multi-source retrieval toolkit (OpenAlex, arXiv, Crossref, Jina, GitHub, Semantic Scholar), a typed claim ledger, a mandatory contradiction pass, and publish gates.

## Zero External API Keys Required (Native Subagent Mode)
1. **Retrieval**: The bundled retrieval toolkit (`tools/retrieval.py`) queries public open-access databases (OpenAlex, arXiv, Crossref, Jina, Semantic Scholar, GitHub) which require **no paid API keys**.
2. **Parallel Retrieval Subagents**: Antigravity spawns native subagents (`invoke_subagent`) using the `peak-retriever` role to run parallel discovery, targeted, contradiction, and gap passes.
3. **Native LLM Adjudication**: Instead of needing third-party API keys (Anthropic/NVIDIA), Antigravity and its subagents directly perform claim typing (Fact/Derived/Inference/Hypothesis/Recommendation) and contradiction triage using Antigravity's native LLM reasoning.

## Quality Gates
- **G1 (Retrieval Health)**: Verifies retrieval returned viable records.
- **G3 (Citation Resolution)**: Every cited source must resolve to a valid primary work (DOI, OpenAlex ID, or arXiv ID).
- **G5 (Artifact Structure)**: The artifact must satisfy strict structure requirements (Executive Summary, Scope/Method, Findings, Contradictions, Open Questions, Sources). A failing run leaves a `.draft.md` and does NOT publish.

## Subagent Roles & Contract

### `peak-retriever` (Leaf Worker)
- **Role**: Dispatched for independent parallel literature searches (up to 10 concurrent).
- **Contract**:
  - Exactly one specific retrieval goal / sub-track.
  - Never spawns child subagents.
  - Runs `tools/retrieval.py` for OpenAlex/arXiv/Crossref/Jina queries.
  - Records failures explicitly instead of substituting.
  - Returns raw structured JSON.

### Orchestrator (Antigravity Main Agent)
- Runs planning (Phase 1).
- Dispatches subagents for passes (Phase 2).
- Extracts and types claims using model reasoning (Phase 3).
- Performs synthesis and contradiction triage (Phase 4).
- Audits and publishes final deliverable to `./research/<topic>.md` (Phase 5).
