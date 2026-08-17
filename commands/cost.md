---
description: Report cumulative API spend for peak-research runs and where the cache lives.
allowed-tools: Bash, Read, Glob
---

Report peak-research API spend.

```
python "${CLAUDE_PLUGIN_ROOT}/skills/peak-research/tools/retrieval.py" cost
```

The counter lives in the cache directory, so it is per-workspace: `./.peak-research/cache/`
by default, or `$PEAK_CACHE_DIR` / `$PEAK_WORKSPACE` if set. Report which workspace the number
came from — a $0.00 reading usually means a fresh workspace, not a free run.

Context for the number:

- OpenAlex runs a credit/USD model with a free budget that resets at midnight UTC. Exhaustion
  gives 429, then 402.
- Full-text `search=` calls cost ~$0.001 each; `title_and_abstract.search` is cheaper and more
  targeted; entity GETs with `select=` are free.
- Cache hits cost $0 and are not counted. Entries expire after 30 days
  (`PEAK_CACHE_TTL_DAYS`; 0 caches forever).
- `--adjudicate` calls are logged at an estimate, not a metered price
  (`PEAK_ADJUDICATION_USD_PER_CALL`, default $0.02). Treat that line as an order of magnitude
  and check the provider's own dashboard for the real figure.

Also report the cache directory size if the user is deciding whether to clear it. Deleting the
cache costs money on the next run and nothing else.
