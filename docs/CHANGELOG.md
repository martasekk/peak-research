# peak-research — changelog

## Model chains, per-task routing, benchmarked defaults (2026-08-17)

1. **`PEAK_ADJUDICATION_MODEL` accepts a chain**, not one id. A retired model (404), a rate
   limit (429), a server error, a timeout, or unparseable output steps to the next entry;
   only 401/403 aborts, since no model behind the same key would do better. Previously a
   single stale id dropped the whole run back to lexical heuristics — which is exactly what
   happened: the shipped default `nvidia/llama-3.1-nemotron-ultra-253b-v1` 404s.

2. **Per-task chains.** `PEAK_ADJUDICATION_MODEL_TYPING` and
   `PEAK_ADJUDICATION_MODEL_CONTRADICTIONS` override the global chain, so bulk classification
   and comparative reasoning can use different models.

3. **Cache is probed for every model in the chain** before any network call, so a corpus
   adjudicated by the second model on a previous run is served from disk rather than
   re-failing the first.

4. **Default chain reordered by measurement**: `nemotron-3-super-120b-a12b` →
   `nemotron-3-ultra-550b-a55b`. Super was faster and better on both tasks; Ultra
   downgraded genuine empirical conflicts to `method-mismatch`, defeating the mandatory
   contradiction pass. `nemotron-3.5-lightning-30b-a3b` is excluded — 20–40× slower per
   claim than Super at every batch size tested (114s/claim at n=3, 58s/claim at n=8, no
   completion at n=20), so shrinking the batch makes it worse rather than viable. See
   `MODEL_BENCHMARK.md`.

5. **The test suite is hermetic again.** It inherited ambient `PEAK_ADJUDICATION_*` env, so
   once the plugin was actually configured the anthropic-stub sections failed —
   `/peak-research:doctor` runs these tests on exactly such a machine. It now clears that
   config at import, and `setenv` knows about the new per-task vars.

6. **A typo in `PEAK_ADJUDICATION_PROVIDER` no longer kills the whole pipeline.** The
   module-level `MODEL` constant resolved at import through `provider()`, which raises on an
   unrecognised value — so `PEAK_ADJUDICATION_PROVIDER=nvida` made *every* import of
   `run_research.py` fail, including plain runs that never asked for adjudication. It now
   degrades to an empty string and `available()` reports the problem where it matters.
   Pre-existing, but far likelier to be hit now that the variable is recommended for
   `settings.json`, where it applies to every session.

7. **`--check` prints the full chain** (`a -> b`, plus per-task overrides) instead of just
   the head, and reports a bad provider or missing key as a one-line status rather than a
   traceback.

8. **The artifact no longer tells you to enable a flag you already enabled.** Phase 4 runs
   the adjudicator only `if adjudicate and contradictions` — with zero lexical candidates it
   is never invoked, and the artifact's "was this adjudicated?" test inferred the answer
   from the (empty) results, so an adjudicated run printed *"Run with `--adjudicate` to have
   these triaged."* Phase 4 now records `adjudication_requested` and the contradiction
   section states plainly that the model was enabled but had nothing to triage — the
   bottleneck there is lexical detection, which `--adjudicate` cannot fix.

Twelve new assertions cover chain parsing, dedup, per-task resolution, 404 fallthrough,
cache-serves-the-winner, and 401-aborts-the-chain.

## Packaged as a Claude Code plugin (2026-08-17)

Structure and portability only. The METHOD, the 20 steps, the gates, the toolkit and the hard
rules are unchanged; `test_gate.py` and `test_adjudicate.py` pass unmodified.

1. **Nothing writes into the install directory.** New `tools/paths.py` resolves every writable
   location — workspace, cache, run state, output — relative to the invocation directory, with
   `PEAK_WORKSPACE` / `PEAK_CACHE_DIR` / `PEAK_OUTPUT_DIR` / `PEAK_CATALOG` overrides and a new
   `--workspace` flag. A plugin install is shared and is replaced wholesale on update, so the
   old `cache/`-next-to-`__file__` and `HERE/runs/` layout could not survive there.

2. **The two `D:\` paths are gone.** `CONFIG["catalog"]` and `CONFIG["output_dir"]` pointed at
   directories that existed on one machine. The catalog is now optional (`PEAK_CATALOG`,
   consulted by METHOD step 6, absent by default) and the output dir defaults to `./research`.

3. **The cache directory is created on first write, not on import.** Importing the toolkit no
   longer creates a directory as a side effect.

4. **Retrieval workers pin the parent's workspace.** Children run with `cwd=HERE`; since path
   resolution falls back to cwd, they would otherwise have cached into the install directory
   while the parent cached into the workspace — two caches, neither shared.

5. **`tools/subagent_retrieve.py` is checked-in source.** It used to be re-emitted on every run
   from a string literal inside `run_research.py` — a second copy to keep in sync, and a write
   into what is now a read-only directory. Its `sys.path` line also pointed at `tools/tools`,
   a no-op that only worked because Python adds a script's own directory to the path.

6. **Dangling cross-links removed.** METHOD.md carried 20 `../peak-research/stages/SkillN.md`
   references to a version this package does not ship.

Added by the packaging: `.claude-plugin/` manifests, five slash commands (`research`,
`retrieve`, `verify`, `cost`, `doctor`), and the `peak-retriever` agent, which carries the same
leaf-only, record-failures-never-substitute contract as the scripted workers.

## Correctness pass (2026-08-17)

Fixes for defects where the pipeline's behaviour did not match its documentation. Each item
has a regression test in `test_gate.py`; all of them fail against the previous code.

### Silently wrong (highest severity)

1. **The contradiction pass had never run.** `detect_contradictions` scored claim polarity with
   `pos_score = sum(1 for w in [<cue list>])` — no membership test, so the claim text was never
   read. Both lists returned constants (31 and 33), `neg_score` always won, `positive` was always
   empty, and the `if positive and negative` guard could never open. Every run reported
   *"No direct contradictions detected"* on a pass METHOD.md calls mandatory.
   Now: real word-boundary matching with negation handling (`"does not improve"` scores negative),
   candidates require different papers on each side, and results are labelled `candidate /
   unresolved — requires human triage` rather than `genuine-empirical`.

2. **Cache keys collided.** `_cache_path` truncated a readable key to 160 chars; 19 of the 44
   cached files were already at that limit. Two URLs sharing a long prefix — the same search
   phrase with a different `per-page` or year — mapped to one file, so one query silently
   returned another's results. Now keyed by `sha1(url)` with a readable prefix, plus a 30-day TTL.

3. **Fulltext cleaning could delete the rest of a document.** `clean_jina_text` ran patterns like
   `r"(?i)timestamp:.*"` under `re.DOTALL`, where greedy `.*` consumes every remaining character.
   A single "Timestamp:" in a bot-check page truncated the whole article. Other patterns
   (`r"(?i)doi"`, `r"(?i)copyright"`) deleted substrings mid-sentence, corrupting the prose that
   claims were then extracted from. Now line-scoped and non-greedy; pages that are mostly
   bot-wall text are rejected outright instead of mined for claims.

### Gates that could not fail

4. **G3 `verify_records`** passed anything with a non-empty title and abstract, so "0 unverified"
   was guaranteed for any record OpenAlex returned. Its `min_abstract` parameter was accepted and
   never used. Now: requires a resolvable identity as well as a title, enforces `min_abstract`
   (short abstracts count as non-citable), and `--live` re-resolves every DOI through Crossref and
   **fails on title mismatch** — the check that actually catches a fabricated or mis-attributed
   citation.

5. **G5 `check_artifact`** substring-matched words like `"source"` and `"fact"` against the whole
   document, so any research-shaped text passed. Now matches real markdown headings, counts
   distinct DOI/URL/arXiv identifiers (≥5), rejects placeholder text, and rejects empty sections.
   *The previously published `b2b-wholesale-negotiation-buyer-psychology.md` passed the old gate
   and fails the new one: placeholder contradiction section plus empty Gaps and Synthesis.*

6. **A failing G5 still published.** `phase5_publish` printed `G5 FAILED` and returned
   `published: True` over the real output path. Now the artifact is written to `.draft.md` and only
   promoted on pass.

### Hardcoded to one topic

7. `TOPIC_KEYWORDS`, `DEFAULT_QUERIES`, ten hardcoded journal names in `expand_search_via_venues`,
   a ~40-entry `venue_prestige` table, the contradiction grouping vocabulary, and the artifact's
   literal closing recommendation (*"run targeted contradiction pass on anchoring vs framing in
   B2B contexts"*) were all specific to one subject — and applied to every `--topic`.
   Now: queries derive from the topic (`derive_queries`), the relevance vocabulary is built from
   the topic and expanded from the retrieved corpus (`build_topic_profile` /
   `expand_topic_profile`), venue weighting is computed from corpus citation distribution
   (`build_venue_prestige`), and next actions are generated from the run's own weak spots.

8. **The gap pass searched for bare words.** Subquestion labels went to OpenAlex verbatim, so the
   pipeline ran global searches for `background`, `risks`, `alternatives`. Now composed with the
   topic stem.

### Honesty of output

9. **Titles were emitted as claims.** Extraction prepended the title to the abstract and split on
   sentences, so every paper contributed its own title as a typed `derived` claim. Now sentences
   must contain an assertive verb, clear a length/prose floor, and not restate the title.

10. **92% of claims were typed `derived` by fallthrough.** Unmatched sentences now type as
    `untyped`, the matched cue is recorded alongside each type, and the untyped share is reported.

11. **The evidence matrix was circular** — its "Evidence" column restated the source's own
    metadata, so each row read *claim X is supported by the paper X came from*. Replaced with
    `extracted_from` (which text, which paper), `source_signal`, and a real `corroboration`
    column marking claims echoed by a different source.

12. **Synthesis reported histograms as findings.** "Key findings" was
    `current_evidence: 132 claims ({'derived': 121, ...})` — a count of the pipeline's own labels.
    Now consensus quotes cross-source statements, confidence is derived from what the run
    achieved, and an empty contradiction section states that the pass is incomplete rather than
    implying agreement.

13. **Citation/venue exemptions defeated relevance screening.** Records were kept on
    `cites > 30 or is_top_venue` regardless of topic — how an oncology paper came to be cited in
    a wholesale-negotiation review (and then added to the prestige table). Off-topic records are
    now screened to `discovery_only`: recorded with a reason, never citable.

14. **Relevance scoring was partly inert.** Multi-word entries could never match single-token
    text, and dividing by the record's own word count penalised longer abstracts. Now phrases are
    matched against raw text, title hits weigh double, and the score is coverage of the profile.

### Found by running the pipeline on an unrelated topic

An end-to-end run on *"retrieval augmented generation for legal document review"* — a topic
the code had never seen — exposed seven more defects, all of which had been present on every
previous run and invisible because the artifact still looked finished.

15. **Three of four gap passes died on every Windows run.** The subagent printed
    `json.dumps(..., ensure_ascii=False)` to a cp1252 console; a minus sign or non-breaking
    hyphen in any abstract raised `UnicodeEncodeError` *after* the API call had been paid for.
    The parent decoded with `text=True` (locale encoding) and hit the mirror-image error.
    Both ends now force UTF-8.

16. **Retrieval failures could not stop the run.** Every failed pass was printed and skipped;
    a run with all four discovery passes crashed still published a report built from five
    stray records. Added **G1**: abort if more than 34% of retrieval tasks fail, if no
    discovery pass succeeds, or if fewer than 5 records survive screening. Failures are
    written to `RETRIEVAL_FAILURES.json`.

17. **The generated subagent script was only written when absent**, so edits to
    `create_subagent_script()` never reached the file actually executed. Now regenerated
    every run.

18. **Author expansion searched author names as free text.**
    `works?search=<display name>` matches a name anywhere in a work — including its
    reference list — so citing an author pulled in arbitrary papers. This is where
    *Dynamic Graph CNN for Learning on Point Clouds* and *HotpotQA* entered a legal-RAG
    corpus. Now `filter=author.id:` against the ID captured in `_work_to_record`.

19. **Generic expanded vocabulary admitted off-topic papers.** Corpus-derived terms
    ('models', 'language', 'dataset', 'graph') were weighted equally with the question's own
    terms, so a paper matching only those cleared the threshold. Added `has_topical_anchor`:
    a record must hit at least one term from the original topic to enter the evidence set,
    and expanded terms now carry one third of a core term's weight. Off-topic records go to
    `discovery_only` with the reason recorded.
    *Effect on the same query: records with no core topic term fell from 8 of 27 to 0; the
    corpus went from point-cloud CNNs and NER benchmarks to Brazilian legal-document RAG,
    Chinese legal judgment generation, CBR-RAG for case law, and legal hallucination
    profiling.*

20. **Extracted fulltext could restructure the artifact.** Claim text was inserted verbatim,
    so a sentence carrying markdown `#` from a fetched PDF became a heading in the
    deliverable and `|` broke evidence-table rows — G5 correctly flagged a paper's author
    block as an "empty section". Claim text is now flattened, and author/affiliation blocks
    (emails, >50% capitalised tokens) are rejected as candidates.

21. **Paper furniture was being extracted as claims — and faked corroboration.** Data- and
    code-availability lines, funding statements, conflict-of-interest declarations and
    "the remainder of this paper is organized as follows" pass an assertive-verb test while
    asserting nothing about the subject. Because they are near-identical across papers, the
    cross-source check matched them to each other: in one run the *only* two "corroborated by
    2 sources" statements were both code-availability lines. Now filtered.
    *Effect: the same run's stated confidence dropped from "Low (2 of 97 corroborated)" to
    "Very low (0 of 85 corroborated)" — which is the true state of that corpus.*

### Housekeeping

22. `.gitignore` for `cache/`, `runs/`, `__pycache__/`, `*.rar`, `*.draft.md`; committed bytecode
    removed. The placeholder `OPENALEX_MAILTO` now warns on startup. `--output-dir` and
    `--live-verify` added. `test_gate.py` rewritten: 47 assertions, no network.
    `_work_to_record` hardened against OpenAlex's present-but-null `author` / `id` fields
    (`.get(k, "")` returns `None` for those, not `""`).

### Not addressed in this pass

- 44 cache files from before the key change are stale under the new scheme — harmless (they
  simply miss and refetch), but `cache/` can be emptied.
- `runs/` still holds 190 files from previous runs.
- Claim typing and contradiction detection remain lexical heuristics — addressed next.

---

## LLM adjudication layer (2026-08-17)

The correctness pass left claim typing and contradiction detection as lexical heuristics and
named an LLM pass as the honest ceiling. This adds it, as an **opt-in** layer.

**`tools/adjudicate.py`** (new) uses the official `anthropic` SDK to replace the two weakest
heuristics:

- **Claim typing** — a model reads each candidate sentence and returns `substantive`, a type,
  a confidence, and its reason. Non-substantive sentences are dropped rather than typed, which
  subsumes the boilerplate filter. Runs at `medium` effort, 20 claims per call.
- **Contradiction triage** — implements METHOD step 13 directly: each lexical candidate is
  classified `genuine-empirical` / `method-mismatch` / `scope-mismatch` / `not-a-conflict`,
  with an explanation naming the specific difference and what would settle it. Candidates
  judged not to be conflicts are dropped. Runs at `high` effort — this is the judgment the
  whole method rests on.

Design constraints held:

- **The rest of the skill stays stdlib-only.** The SDK dependency lives in this one module —
  and the OpenAI-compatible backend added below needs no dependency at all.
- **Opt-in** (`--adjudicate`), because it spends money. Without the flag nothing changes.
- **Degrades, never crashes.** Missing SDK, missing credentials, rate limits, API errors,
  safety refusals, truncated responses, and unparseable JSON all return `None`, and the
  caller keeps its heuristic result. Verified for each failure mode in `test_adjudicate.py`.
- **Cached by exact prompt** under `cache/`, so re-running a corpus costs $0; spend is logged
  to the same cost log as retrieval. The instruction block is identical across batches and
  carries `cache_control`, so every call after the first reads the prefix at ~0.1×.
- **Provenance is visible end to end.** Adjudicated types carry `llm-<confidence>` rather
  than `heuristic`, adjudicated conflicts carry `verification: llm-adjudicated`, and the
  artifact's method note and confidence paragraph state which path produced the section —
  an adjudicated finding and a lexical guess never look alike.

### Second backend: any OpenAI-compatible endpoint

Added after the first pass, so the layer isn't locked to one vendor. `PEAK_ADJUDICATION_PROVIDER=nvidia`
(or `openai`/`nim`/`vllm`/`ollama`/`openrouter`/`together`/`groq` — all aliases for the same
transport) routes to a `/chat/completions` endpoint over **stdlib urllib, adding no dependency**.
`openai` names the wire protocol, not the vendor; nothing reaches OpenAI unless `base_url`
points there.

The hard part is that the Anthropic path gets schema-valid JSON by construction and these
servers do not. That path therefore:

- degrades `response_format` through `json_schema` → `json_object` → no hint, since servers
  variously enforce, ignore, or 400 on each — while stopping immediately on 401/403/404,
  where stepping down only wastes calls;
- repeats the schema in the prompt, because a server that ignores the parameter still has
  to be told what to emit;
- strips reasoning traces (`<think>` blocks, including unclosed ones from truncated output)
  and markdown fences before parsing, then extracts the outermost balanced JSON object
  respecting strings and escapes;
- drops malformed rows and out-of-vocabulary labels instead of failing the batch — a dropped
  row simply keeps its heuristic value;
- catches `finish_reason` of `length` / `content_filter` and reasoning-only responses, rather
  than parsing them as JSON.

Auth defaults to `Authorization: Bearer <key>` — the OpenAI-wire standard, and what NVIDIA's
hosted endpoint expects with an `nvapi-` key. `PEAK_ADJUDICATION_AUTH_HEADER`,
`_AUTH_PREFIX`, and `_EXTRA_HEADERS` cover gateways that differ.

`python tools/adjudicate.py --check` makes one probe call and reports whether the model
separated an empirical claim from a data-availability statement — so a wrong model id, bad
key, or unusable model surfaces before a full run rather than during one.

**Bug found and fixed in this module during testing:** `provider()` tested `if _openai_key():`
where `_openai_key()` returns a `(key, source)` tuple — and `("", "")` is truthy, so provider
inference always chose `openai`. Same class of defect as the original contradiction bug
(a truth test on a container instead of its contents). Caught because the probe selected the
wrong provider on a machine with no keys set; now asserted directly.

`test_adjudicate.py` (new): 105 assertions against a stub client injected into `sys.modules`
and a stubbed HTTP transport —
batching, index mapping across batch boundaries, malformed-index handling, prompt assembly,
schema conformance, cache hit/miss, cost logging, every failure path, non-mutation of the
caller's ledger, provider selection and aliases, auth-header overrides, the `response_format`
degradation ladder, and nine shapes of messy model output (fences, prose, reasoning traces,
braces inside strings). No network, no credentials, no SDK required.

### Not addressed

- **The adjudication layer is unvalidated on real output, on either backend.** Its plumbing
  is tested; the quality of the model's judgments is not — no live call was possible here (no
  SDK, no credentials on this machine). This matters more now that a second backend exists:
  contradiction triage is a hard judgment task, and a cheaper model may do it markedly worse
  while producing identically well-formed JSON. `--check` catches "unusable"; it cannot
  measure "good". That needs a hand-labelled sample scored against each backend's verdicts,
  which is also the way to compare providers.
- Without `--adjudicate`, claim typing and contradiction detection remain lexical heuristics
  that read wording, not argument structure. The code labels its own confidence rather than
  pretending otherwise.
