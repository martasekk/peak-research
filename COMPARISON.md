# peak-research v1 vs v2 — Side-by-Side Comparison

> Both skills are preserved on disk (`peak-research/` = v1, `peak-research-v2/` = v2) for direct
> inspection. This document compares them and extracts the best practices from each into a merged
> recommendation (see "Merged best-of-both" below).

## Structure

| Aspect | v1 (peak-research) | v2 (peak-research-v2) |
|---|---|---|
| Stage files | 20 separate `stages/SkillN.md` (copied verbatim, "no shortening") | ONE `METHOD.md` with 5 phases / 20 steps inline |
| Config | Scattered through `SKILL.md`; catalog path was WRONG (`Downloads/...`) | Dedicated `CONFIG.md` (paths, mailto, fallback, subagent contract, gates) |
| Orchestrator | `run_demo.py` (demo only) + `test_gate.py` | `run_research.py` (real phase driver) + `test_gate.py` |
| Toolkit | `tools/retrieval.py` (added later, bolted on) | `tools/retrieval.py` (first-class, copied + improved) |
| References | `retrieval_catalog.md`, `methodology_principles.md` | same, + this `COMPARISON.md` |
| Entry point | `SKILL.md` lists 20 stages + tool notes | `SKILL.md` → CONFIG + METHOD + tools |

## Strengths

**v1 strengths**
- *Granularity:* each stage is a self-contained file with its own JSON I/O schema — easy to edit one step without touching others.
- *Fidelity:* "copied verbatim from curated source" means the method is the canonical one the user originally authored/approved.
- *Batteries:* the toolkit it accumulated (OpenAlex DOI fix, fallback chain, S19 verify gate) is battle-tested.

**v2 strengths**
- *Coherence:* one top-to-bottom METHOD reads as a procedure, not 20 jumps. Faster to load for an agent (one file vs 20).
- *Config externalized:* changing topic/path/mailto touches one file, not scattered prose.
- *Runnable:* `run_research.py` actually drives Plan→Retrieve→Audit; v1 only demoed it.
- *Same safety:* identical toolkit, hard rules, and audit gate — the restructuring changed structure, not discipline.

## Weaknesses

**v1 weaknesses**
- *Load cost:* 20 files to load for one research run (context-heavy).
- *Drift risk:* catalog path was wrong; fixes lived in prose, easy to miss.
- *No runner:* synthesis was agent-only; no script enforced the phase order.
- *No config seam:* knobs buried in narrative.

**v2 weaknesses**
- *Edit friction:* editing one step means editing inside a big METHOD file (vs v1's per-file).
- *Risk of silent shortening:* collapsing 20 files into one invites accidental condensation of a step's nuance (mitigated by keeping v1 verbatim alongside).
- *Newer:* less field-hours than v1; the 20-step content is preserved but not independently re-validated step-by-step.

## How they differ (summary)
v2 is **v1's method with the scaffolding re-architected**: 20 files → 1 METHOD + 1 CONFIG + 1 orchestrator. The *content* (20 steps, toolkit, hard rules, audit gate) is the same. v1 wins on edit-granularity and canonical fidelity; v2 wins on coherence, configurability, and runnability.

## Merged best-of-both (implemented)
To get the upsides of both without the weaknesses, the recommended final form is:

1. **Keep v1's 20 verbatim stage files** as the canonical, per-step reference (edit-granularity + fidelity). → Already in `peak-research/stages/`.
2. **Adopt v2's METHOD.md** as the agent-facing quick procedure (coherence + low load). → In `peak-research-v2/METHOD.md`.
3. **Adopt v2's CONFIG.md** as the single knob surface (path/mailto/fallback/subagent/gates). → Corrected catalog path `D:\peak-search\...` here.
4. **Adopt v2's run_research.py** as the enforced runner (order + gates). → v2.
5. **Keep the shared toolkit** `tools/retrieval.py` (with `select=` cost fix + retry + verify gate) in BOTH.
6. **Cross-link**: v2 METHOD references v1 stages for deep-dive on any step ("for full schema see `../peak-research/stages/Skill8.md`").

This gives: one fast read for the agent, one deep file per step for editing, one config, one runner, one toolkit, one audit gate. The "merged" skill = v2's shell wrapping v1's verbatim stages.

## Verification status
- v1: `test_gate.py` → ALL GATE TESTS PASS (after duplicate `openalex_by_id` removed).
- v2: `run_research.py` live run → 6 records, audit `pass: true`; `test_gate.py` → ALL PASS.
- Both tools confirmed against live OpenAlex/arXiv/GitHub/Jina/Crossref during the hardening pass.

---

# Improvement Pass — Baseline (v2) vs Improved (v2+)

After the merge, I ran a second improvement pass on v2 and measured it against the **baseline**
snapshot saved in `_baseline/`. Improvements are real, not cosmetic — each is verified live.

## What changed (improved = baseline + these)
1. **Disk cache + cost tracking** (`tools/retrieval.py`): every API response cached under `cache/`;
   `cost_summary()` logs cumulative USD. → Re-runs cost **$0** (no network), directly conserving the
   OpenAlex credit/USD budget the subagents discovered.
2. **Four-pass retrieval enforced** (`run_research.py` phase2): discovery → targeted → contradiction
   → gap, each logged (baseline only did discovery + targeted).
3. **G5 artifact self-check gate** (`check_artifact`): verifies the final `.md` has all required
   sections + a typed claim ledger + sources; fails publish otherwise (baseline had no such gate).

## Before / After (measured)
| Metric | Baseline (v2) | Improved (v2+) | Delta |
|---|---|---|---|
| Retrieval passes run | 2 (discovery, targeted) | 4 (+, contradiction, gap) | +2 passes |
| Records retrieved (same topic) | 6 | 16 | +167% coverage |
| Re-run cost | re-spends on every query | $0 (cache hit) | budget conserved |
| Publish-time artifact check | none | G5 structural gate | +safety |
| Gates total | G1–G4 | G1–G5 | +1 |
| `test_gate.py` | ALL PASS | ALL PASS | unchanged (no regression) |

## Residual differences from v1 (intentional, by design)
- v1 keeps 20 separate stage files (edit-granularity + canonical fidelity); v2+ keeps ONE METHOD +
  cross-links to v1's verbatim stages. Both preserved on disk.
- v1 has no runner/orchestrator; v2+ does (`run_research.py`). v1's demo (`run_demo.py`) still passes.
- The merged recommendation (v2 shell wrapping v1 stages) remains the recommended final form.

## Honest limits (unchanged)
- OpenAlex free budget is credit/USD-gated (resets midnight UTC) — now mitigated by cache + `select=`,
  not eliminated.
- Methodology principles (12) remain design heuristics, not measured benchmarks.
- G5 is a *structural* check (sections/ledger present), not a semantic correctness check — a well-
  formed but wrong artifact still passes. That is by design: semantic judgment stays with the agent.

