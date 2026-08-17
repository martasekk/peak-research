# peak-research — Retrieval Toolkit & Source Catalog (VALIDATED)

> All patterns below were tested live against the real APIs in Aug 2026. This is the
> canonical reference for stages 6/7/8 (source map, strategy, retrieve). The skill ships
> a working toolkit at `tools/retrieval.py` — prefer it over hand-rolled curl.

## 0. CURATED CATALOG (OPTIONAL)
If you keep a curated source list, point `PEAK_CATALOG` at it and METHOD step 6 (source map)
will consult it. None is bundled — a plugin must not depend on one machine's filesystem — and
its absence degrades source selection rather than breaking a run. Rule #6 holds either way:
never cite an aggregator or abstract when the original is available.

## 1. Tool priority / fallback chain (when primary is blocked)
Empirically-validated order (live-tested Aug 2026):
```
Crossref (DOI verify, cheap+reliable)
  → arXiv API (https + UA, retry once)
  → r.jina.ai reader (web/HTML → Markdown; tolerate 401-warned partials)
  → raw curl -sL (plain HTML only)
  → GitHub API (needs UA; watch 60/hr; auth for code search)
  → Semantic Scholar (optional citation enrichment; expect 429)
```
If ALL fail: flag honestly, drop the source, do NOT fabricate. Firecrawl is usually
billing-blocked (402) — treat as unavailable; never block the run on it.

## 2. OpenAlex (api.openalex.org) — VALIDATED PATTERNS
Always append `&mailto=you@example.com`. Docs: https://docs.openalex.org

- Basic full-text search (hits FULL TEXT, not just title):
  `?search=KEYWORD&per-page=3&mailto=...`
- Title+abstract phrase (SHORT stems only; long phrases → 0 hits):
  `?filter=title_and_abstract.search:"short phrase",from_publication_date:2022-01-01&sort=cited_by_count:desc&per-page=5&mailto=...`
- ⚠️ `filter=arxiv:2303.11366` is REJECTED. Use the arXiv DOI form instead:
  `?filter=doi:10.48550/arxiv.2303.11366&mailto=...`
- By DOI: `?filter=doi:10.1038/s41586-021-03819-2&mailto=...`
- By OpenAlex ID: `/works/W4353112996?mailto=...`
- Pagination: `&cursor=*` then use `meta.next_cursor` (per-page up to 200).
- Cost control: add `&select=id,title,cited_by_count` to shrink response + cost.
- Abstract lives in `abstract_inverted_index` (word -> [positions]); reconstruct by sorting positions.
- Citation count: `cited_by_count`.
- Confirmed valid filters: `publication_year:2023`, `type:article`,
  `cited_by_count:>100`, `from_publication_date:2023-01-01`, `authorships.author.id:A...`.

### OpenAlex cost/rate model (changed — IMPORTANT)
OpenAlex now runs a **credit/USD cost model**. Responses include:
`X-RateLimit-Cost-USD`, `X-RateLimit-Remaining-USD`, `X-RateLimit-Reset` (seconds).
At exhaustion you get HTTP 429/402 with JSON `{"error":"Rate limit exceeded",...}`.
The free daily budget resets at **midnight UTC**. Mitigations:
- Use `select=` to fetch only needed fields.
- Prefer `title_and_abstract.search` over `search=` (cheaper, more targeted).
- Cache results to `tools/../cache/` so a re-run does not re-spend credits.
- If budget is exhausted, fall back to Jina/arXiv/GitHub and re-run OpenAlex next UTC day.

## 3. r.jina.ai reader — VALIDATED
`curl -s https://r.jina.ai/https://<URL>`
- Returns clean Markdown with `Title:` and `Abstract:` for arxiv + most pages.
- YouTube/listing pages: returns HTTP 200 but a `Warning: 401 Unauthorized` body — use the
  youtube-content skill (youtube-transcript-api) for those instead.
- Primary fallback when a publisher blocks bots or OpenAlex abstract is elided.

## 4. arXiv API (export.arxiv.org) — VALIDATED
- By ID: `http://export.arxiv.org/api/query?id_list=2303.11366&max_results=1`
  → parse `<entry><title><summary><published>`.
- Title search: `search_query=ti:%22PHRASE%22&max_results=3`
- ⚠️ API is INTERMITTENTLY FLAKY: sometimes returns a query-echo page with NO `<entry>`.
  Detect by checking for `<entry>`; if absent, retry or fall back to OpenAlex-by-DOI.

## 5. GitHub API (api.github.com, no auth ~60/hr) — VALIDATED
- Search repos: `/search/repositories?q=KEYWORD&per_page=5`
- Repo meta: `/repos/OWNER/REPO`
- Org repos: `/orgs/OWNER/repos?per_page=100`
- Repo contents: `/repos/OWNER/REPO/contents`
- Code search: `/search/code?q=TERM` → usually 401 without auth; skip if blocked.
- To confirm a company owns a repo: check `/orgs/ORG/repos` and inspect descriptions;
  unrelated orgs with similar names are common (e.g., `getspine` org ≠ getspine.ai product).

## 6. Crossref (api.crossref.org, no key) — VALIDATED
`https://api.crossref.org/works/10.1038/s41586-021-03819-2` → returns `title`.
Use to verify a DOI resolves before citing it.

## 7. Toolkit usage (tools/retrieval.py)
```
python tools/retrieval.py openalex "short phrase" 2023 5     # year or YYYY-MM-DD
python tools/retrieval.py arxiv 2303.11366                   # canonical abstract
python tools/retrieval.py jina https://example.com           # page reader
python tools/retrieval.py github_repo owner/repo
python tools/retrieval.py github_search "query" 5
python tools/retrieval.py crossref 10.1038/s41586-021-03819-2
python tools/retrieval.py sscholar 2303.11366
python tools/retrieval.py verify records.json               # G3 gate: every cited source resolves
python tools/retrieval.py verify records.json --live        # + re-resolve each DOI via Crossref
python tools/retrieval.py check_artifact final.md           # G5 gate: artifact structure
python tools/retrieval.py cost                              # cumulative USD spent
```
Inside a plugin install, prefix these with
`${CLAUDE_PLUGIN_ROOT}/skills/peak-research/`.

The module is import-safe: `from tools.retrieval import openalex_search, arxiv_abstract,
github_search_repos, verify_records, resolve_paper, resolve_url`. All network calls retry
transient 429/500/connection errors 3× with exponential backoff. Failures raise
`RetrievalError` (never silent). `resolve_paper(axid)` tries OpenAlex-by-DOI then arXiv API.

## 8. Subagent retrieval contract (max 3 concurrent; leaf only)
Applies to the scripted workers (`tools/subagent_retrieve.py`) and to the `peak-retriever`
agent this plugin ships. Full contract in `CONFIG.md`.
- Each child gets: explicit goal, the toolkit path, a fixed list of source types to use,
  and the requirement to return PRIMARY findings (title/abstract/citation) as JSON.
- Children use the toolkit and terminal only.
- They must RECORD failures (e.g., "arxiv returned empty") rather than substitute.
- Parent merges children's JSON into one evidence table and runs `verify_records` (G3).
- Never dispatch >3 children; never let a child ask the user questions.
