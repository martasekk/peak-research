---
description: Check the peak-research install — tests, path resolution, API identity, and the optional LLM adjudication wiring.
allowed-tools: Bash, Read, Glob, Grep
---

Check that peak-research is wired up correctly. Run these and report a short pass/fail table.

**1. Offline test suites** — neither needs network, credentials, or the `anthropic` package:

```
cd "${CLAUDE_PLUGIN_ROOT}/skills/peak-research" && python test_gate.py
```

```
cd "${CLAUDE_PLUGIN_ROOT}/skills/peak-research" && python test_adjudicate.py
```

**2. Where writes will go** — confirm nothing resolves inside the plugin install:

```
python -c "import sys; sys.path.insert(0, r'${CLAUDE_PLUGIN_ROOT}/skills/peak-research/tools'); import paths as P; print('workspace', P.workspace()); print('cache    ', P.cache_dir()); print('runs     ', P.runs_dir()); print('output   ', P.output_dir()); print('catalog  ', P.catalog_path())"
```

**3. API identity** — read `OPENALEX_MAILTO`. Unset or `research@example.com` means anonymous
rate limiting instead of the OpenAlex polite pool. Say so; it is a warning, not a failure.

**4. Adjudication wiring** — only if the user has set a provider or key. This makes one real,
billable probe call, so ask first unless they explicitly asked to test it:

```
cd "${CLAUDE_PLUGIN_ROOT}/skills/peak-research" && python tools/adjudicate.py --check
```

Without `--check` the same script prints the resolved provider, the full model chain
(`a -> b`, plus any per-task overrides) and the base URL without calling anything — prefer
that when the user just wants to see the configuration. Report the whole chain, not just the
head: the fallbacks are what keep a retired model id from silently degrading a run to
lexical heuristics.

Report what each check proves and what it does not. Passing tests mean the gates and the
adjudication fallbacks behave as specified; they say nothing about whether the retrieval APIs
are reachable right now. If the user wants that, run a single cheap live query:
`python tools/retrieval.py openalex "attention" 2023 1`.
