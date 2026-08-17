---
description: Search the literature for a topic without running the full pipeline — papers, abstracts, DOIs.
argument-hint: <topic or short search stem> [year] [count]
allowed-tools: Bash, Read, Glob, Grep
---

Find sources for: **$ARGUMENTS**

This is the ad-hoc retrieval path — no plan, no ledger, no artifact, no gates. Use it to scout
a topic before committing to `/peak-research:research`.

Toolkit (prefix each with `${CLAUDE_PLUGIN_ROOT}/skills/peak-research/`):

```
python tools/retrieval.py openalex "short stem" 2023 5   # SHORT stems only — long phrases return 0 hits
python tools/retrieval.py arxiv 2303.11366
python tools/retrieval.py crossref 10.1038/s41586-021-03819-2
python tools/retrieval.py jina https://example.com
python tools/retrieval.py github_search "query" 5
python tools/retrieval.py sscholar 2303.11366
```

How to work:

1. Derive 3–5 short search stems from the topic. Long natural-language phrases match nothing
   in OpenAlex `title_and_abstract.search`.
2. Run them. Responses are cached, so re-running a stem is free.
3. If a source is blocked, walk the fallback chain from `CONFIG.md`: Crossref → arXiv →
   r.jina.ai → raw fetch → GitHub → Semantic Scholar. Firecrawl is usually 402; treat it as
   unavailable.
4. Report what you found as a table: title, year, citations, venue, DOI.

Rules that still apply here:

- Record failures. If arXiv returned empty, say so — never substitute a different source and
  present it as the one requested.
- Never fabricate a DOI, title, or quote. Drop or flag anything unretrievable.
- A search snippet is a lead, not evidence. Do not cite from a hit list alone; fetch the
  abstract or the paper.
- Prefer the original over an abstract over an aggregator.
