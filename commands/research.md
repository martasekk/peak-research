---
description: Run the full gated research pipeline on a topic and publish a citable artifact.
argument-hint: <research question> [--archetype literature_review|architecture_decision|high_stakes_factual] [--adjudicate]
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Skill
---

Run the peak-research pipeline on: **$ARGUMENTS**

Before running anything:

1. Invoke the `peak-research` skill so the METHOD and its hard rules are loaded. Read
   `${CLAUDE_PLUGIN_ROOT}/skills/peak-research/METHOD.md` and `CONFIG.md` if the skill body
   alone leaves the phase order or the gates unclear.
2. Separate the research question from any flags the user passed in `$ARGUMENTS`. If they
   named no archetype, pick one and say which you picked and why:
   - `literature_review` — broad, cite-heavy, recency-weighted (the usual default)
   - `architecture_decision` — options and tradeoffs, fewer but deeper sources
   - `high_stakes_factual` — legal / medical / financial / safety; forces `--live-verify`
     and flags the artifact for human review
3. Check `OPENALEX_MAILTO`. If it is unset or still the placeholder, tell the user the run
   will fall out of the OpenAlex polite pool into anonymous rate limiting, and continue.

Then run:

```
python "${CLAUDE_PLUGIN_ROOT}/skills/peak-research/run_research.py" --topic "<question>" --archetype <archetype> [--live-verify] [--adjudicate]
```

Run state lands in `./.peak-research/runs/<topic-slug>/` and the deliverable in `./research/`
unless the user set `PEAK_WORKSPACE` / `PEAK_OUTPUT_DIR` or passed `--output-dir`. The run
makes live API calls and can take several minutes; do not silently retry a whole run that
failed a gate.

When it finishes, report honestly against what the gates actually prove:

- **Exit 2 at G1** — the corpus was too broken to build on. No artifact was written. Say what
  failed (`RETRIEVAL_FAILURES.json` in the run dir) rather than re-running with looser terms.
- **Exit 2 at G5** — a `.draft.md` exists and was NOT published. Report which sections were
  missing or empty.
- **Published** — give the path, the record count, the share of `untyped` claims, and whether
  adjudication was active or the run fell back to lexical heuristics.

Then state the standing caveat in your own words: G3 proves each citation resolves to the work
recorded, G5 proves the artifact has the required shape. Neither judges whether a claim is
true, whether an extracted sentence means what it appears to out of context, or whether the
corpus represents the literature. An empty contradiction section on a non-adjudicated run
means the heuristic found nothing — not that the literature agrees.
