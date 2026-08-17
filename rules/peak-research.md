# Research Quality Standards (peak-research)

1. **Evidence-First Verification**: Every factual claim in a research artifact must be backed by a verified, resolvable primary citation.
2. **Typed Claim Ledger**: Explicitly categorize claims into facts, derived metrics, inferences, hypotheses, and recommendations.
3. **Mandatory Contradiction Analysis**: When literature presents differing findings, distinguish genuine empirical contradictions from scope, methodology, or measurement differences.
4. **Publish Gating**: Never publish an unverified or structurally deficient research deliverable. If Gate G5 fails, preserve the draft for correction.
5. **Mandatory Subagent Dispatch**: When executed by an agent, retrieval passes (discovery, targeted, contradiction, gap) must be dispatched in parallel via native subagents (`invoke_subagent` with `peak-retriever` or `research` role, up to 10 concurrent) adhering strictly to the leaf-only retrieval contract.
