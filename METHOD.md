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
- **19. Audit** — run `python tools/retrieval.py verify EVIDENCE_FILE --live`. Severity: Critical=don't publish / Major=revise / Moderate. Gate **G3: 0 unverified sources AND 0 DOI/title mismatches**. Without `--live` the check is structural only and cannot catch a mis-attributed citation.
  *(deep schema: `../peak-research/stages/Skill19.md`)*
- **20. Publish & Version** — save to OUTPUT_DIR; version; set human-review flag for high-stakes. Then run the **G5 artifact self-check**: `python tools/retrieval.py check_artifact <final.md>` — it fails the publish if any required section (brief / subquestions / evidence / claim ledger / contradiction / gaps / synthesis / audit / sources) is missing or no typed ledger/sources are present.
  *(deep schema: `../peak-research/stages/Skill20.md`)*

---

## Tooling guarantees
- **Disk cache**: every OpenAlex/arXiv/Jina/GitHub response is cached under `cache/`, keyed by
  a hash of the full URL (a truncated readable key used to collide) with a 30-day TTL
  (`PEAK_CACHE_TTL_DAYS`, 0 disables). Cache hits cost **$0**.
- **Cost tracking**: `python tools/retrieval.py cost` reports cumulative USD spent.
  Search calls cost ~$0.001; `select=` entity fetches are free. Budget resets at midnight UTC.
- **Four-pass retrieval is enforced** by `run_research.py` (discovery → targeted → contradiction → gap),
  each pass logged, followed by citation / author / venue expansion.
- **Topic independence**: the query set, relevance vocabulary, venue weighting and contradiction
  grouping terms are all derived from `--topic` at runtime and expanded from the retrieved
  corpus. Nothing in the pipeline is specific to a subject area.
- **Optional LLM adjudication**: `--adjudicate` replaces cue-based claim typing and lexical
  contradiction detection with a model that reads the sentences (`tools/adjudicate.py`,
  needs the `anthropic` SDK, or any OpenAI-compatible endpoint with no dependency). Degrades to the heuristics on any failure; the artifact states
  which path produced each section.
- **Gates**: G1 (retrieval health: aborts on >34% failed passes, no discovery pass, or <5 records) · G2 (typed ledger) · G3 (`verify_records`; add `--live`
  to re-resolve every DOI through Crossref and fail on title mismatch) · G4 (human review for
  high-stakes; forces `--live-verify`) · G5 (`check_artifact`: real headings, ≥5 distinct
  identifiers, no placeholder text, no empty sections — a G5 failure blocks publication and
  leaves a `.draft.md`).

## What the gates cannot tell you
G3 proves a citation *resolves to the work you recorded*. G5 proves the artifact has the
*shape* the METHOD requires. Neither evaluates truth, context, study quality, or corpus
completeness — and a well-formed, wrong artifact passes both. Steps 10–17 are the analyst's
work; the script prepares the material for them and marks its own confidence honestly.

Deliverable: final `.md` (+ optional styled `.html`). Gate G4 for high-stakes.

---

## How this differs from v1 (peak-research)
- **One METHOD file, not 20 stage files** — v1 required loading 20 files; v2 is one top-to-bottom read.
- **CONFIG.md** externalizes knobs v1 scattered through SKILL.md.
- **run_research.py orchestrator** drives all 5 phases + gates (v1 only shipped a demo).
- **Same toolkit, same 20 steps, same hard rules** — structure changed, content preserved.
- v1 is kept intact at `../peak-research/` for side-by-side comparison.
