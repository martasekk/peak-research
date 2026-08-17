# peak-research-v2 — METHOD (5 phases, 20 steps)

Evidence-first deep-research pipeline. Restructured from v1's 20 separate stage files into a
single coherent 5-phase narrative: **Plan → Retrieve → Extract → Synthesize → Audit/Publish**.
The 20 granular steps are preserved as the per-phase checklist (nothing shortened), but live in
ONE file so the method reads top-to-bottom instead of across 20 files. All retrieval goes
through `tools/retrieval.py` (validated, retry-on-transient, never-silent). Config lives in
`CONFIG.md`. Run with `python run_research.py --topic "<q>"`.

> HARD RULE: Every material claim gets a citation that actually supports the wording. Separate
> facts / inference / recommendation (typed ledger). Contradiction pass is mandatory. Do NOT
> fabricate sources, DOIs, or quotes — if a source can't be retrieved, drop or flag it.

---

## PHASE 1 — PLAN  (steps 1-5)
Goal: a written, scope-bounded objective before any search.

- **1. Objective** — decision/audience/success criteria. What would a smart skeptic accept as answer?
  *(deep schema: `../peak-research/stages/Skill1.md`)*
- **2. Scope** — date range, geography, depth, acceptable source types, output format. (Archetype from CONFIG tunes this.)
  *(deep schema: `../peak-research/stages/Skill2.md`)*
- **3. Subquestions** — decompose into atomic, independently-answerable tracks (background / current-evidence / alternatives / risks). *Principle: dynamic graph — let tracks grow from results, not a fixed plan.*
  *(deep schema: `../peak-research/stages/Skill3.md`)*
- **4. Concepts & Metrics** — define every measure so claims don't conflate (e.g. "cost per turn" ≠ "cost per session").
  *(deep schema: `../peak-research/stages/Skill4.md`)*
- **5. Protocol** — method, retrieval passes, tool list, reproducible queries. Write the query list NOW.
  *(deep schema: `../peak-research/stages/Skill5.md`)*

Deliverable: `PLAN.md` (objective + subquestions + query list). Gate: objective is specific enough to fail.

## PHASE 2 — RETRIEVE  (steps 6-9)
Goal: a verified source archive. Use `tools/retrieval.py` for every fetch.

- **6. Source Map** — primary > derivative; official > aggregator. Record URL/date/author/type. Use the curated catalog (`D:\peak-search\...`).
  *(deep schema: `../peak-research/stages/Skill6.md`)*
- **7. Strategy & Queries** — four-pass retrieval: **(a) discovery** (broad), **(b) targeted** (precise), **(c) contradiction** (seek dissent), **(d) gap** (fill uncovered sub-claims).
  *(deep schema: `../peak-research/stages/Skill7.md`)*
- **8. Retrieve & Preserve** — execute; capture raw + parameters + timestamp. On failure: retry once, then fallback chain (CONFIG), then flag. *No snippet-as-evidence — fetch and read in context.*
  *(deep schema: `../peak-research/stages/Skill8.md`)*
- **9. Dedupe & Screen** — drop dups; screen by relevance/date/quality/independence. Keep discovery-only vs evidence sources separate.
  *(deep schema: `../peak-research/stages/Skill9.md`)*

Deliverable: `EVIDENCE_FILE` (records keyed by source ID, each with title+abstract+type+year+citations). Gate G1.

## PHASE 3 — EXTRACT  (steps 10-12)
Goal: claim-centered evidence, not source-order notes.

- **10. Assess Quality** — primary/derivative; conflict-of-interest; reproducibility.
  *(deep schema: `../peak-research/stages/Skill10.md`)*
- **11. Extract Evidence** — table: claim | evidence | source | strength | caveat.
  *(deep schema: `../peak-research/stages/Skill11.md`)*
- **12. Claim Ledger** — type each claim: fact / derived / inference / hypothesis / recommendation. Inferences flagged non-factual. (Typed ledger = the spine of the audit.)
  *(deep schema: `../peak-research/stages/Skill12.md`)*

Deliverable: claim ledger JSON. Gate G2.

## PHASE 4 — SYNTHESIZE  (steps 13-17)
Goal: decision-ready findings with disagreements visible.

- **13. Contradictions** — triage: genuine empirical disagreement ≠ method/definition mismatch. Reconcile by checking population, metric, timeframe BEFORE calling it a conflict.
  *(deep schema: `../peak-research/stages/Skill13.md`)*
- **14. Gaps** — claims lacking direct evidence; open questions.
  *(deep schema: `../peak-research/stages/Skill14.md`)*
- **15. Themes** — group by research question, not source order; compare directly.
  *(deep schema: `../peak-research/stages/Skill15.md`)*
- **16. Synthesize** — consensus / disputed / uncertain / missing. Recommendation only after comparison.
  *(deep schema: `../peak-research/stages/Skill16.md`)*
- **17. Conclusions** — decision-ready; confidence + limitations visible.
  *(deep schema: `../peak-research/stages/Skill17.md`)*

Deliverable: synthesis section. *Principle: separate planner/synthesizer from retriever bounds context drift.*

## PHASE 5 — AUDIT & PUBLISH  (steps 18-20)
Goal: a citable artifact that survives scrutiny.

- **18. Artifact** — exec answer → scope/method → findings → evidence/disagreement → risks → recommendation → sources → open questions.
  *(deep schema: `../peak-research/stages/Skill18.md`)*
- **19. Audit** — run `python tools/retrieval.py verify EVIDENCE_FILE`. Severity: Critical=don't publish / Major=revise / Moderate. Gate **G3: 0 unverified sources**. (Also: every cited URL resolves and says what you claim.)
  *(deep schema: `../peak-research/stages/Skill19.md`)*
- **20. Publish & Version** — save to OUTPUT_DIR; version; set human-review flag for high-stakes.
  *(deep schema: `../peak-research/stages/Skill20.md`)*

Deliverable: final `.md` (+ optional styled `.html`). Gate G4 for high-stakes.

---

## How this differs from v1 (peak-research)
- **One METHOD file, not 20 stage files** — v1 required loading 20 files; v2 is one top-to-bottom read.
- **CONFIG.md** externalizes knobs v1 scattered through SKILL.md.
- **run_research.py orchestrator** drives all 5 phases + gates (v1 only shipped a demo).
- **Same toolkit, same 20 steps, same hard rules** — structure changed, content preserved.
- v1 is kept intact at `../peak-research/` for side-by-side comparison.
