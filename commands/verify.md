---
description: Run the publish gates — check that every cited source resolves (G3) and that an artifact has the required structure (G5).
argument-hint: <path to EVIDENCE.json or a research .md>
allowed-tools: Bash, Read, Glob, Grep
---

Verify: **$ARGUMENTS**

Pick the gate from what the path points at. If `$ARGUMENTS` is empty, look for a recent run
under `./.peak-research/runs/` (or `$PEAK_WORKSPACE/runs/`) and ask which one to check rather
than guessing.

**A `.json` evidence file → G3, source resolution:**

```
python "${CLAUDE_PLUGIN_ROOT}/skills/peak-research/tools/retrieval.py" verify <path> --live
```

`--live` re-resolves every DOI through Crossref and fails on a title mismatch. Without it the
check is structural only: it confirms each record has an identifier and a resolved
title/abstract, and **cannot** detect a citation attributed to the wrong work. Use `--live`
unless the user asks for the offline check.

**A `.md` artifact → G5, structure:**

```
python "${CLAUDE_PLUGIN_ROOT}/skills/peak-research/tools/retrieval.py" check_artifact <path>
```

Passes only if every required section is present as a real heading (brief, subquestions,
evidence, claim ledger, contradiction, gaps, synthesis, audit, sources), a typed ledger is
present, there are ≥5 distinct source identifiers, and there is no placeholder text or empty
section.

Report the JSON verdict plainly — which records failed, which sections are missing — and do
not describe a failing artifact as nearly passing. Then note the limit: these gates check
resolution and shape. A well-formed artifact full of wrong claims passes both.
