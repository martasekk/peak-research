#!/usr/bin/env python3
"""
peak-research/run_research.py — the five-phase orchestrator

Drives METHOD.md end to end and refuses to publish an artifact that fails a gate:

  Phase 1 PLAN        objective, scope, subquestions, the query list
  Phase 2 RETRIEVE    four passes (discovery / targeted / contradiction / gap) run by
                      up to 3 leaf workers, then citation / author / venue expansion
  Phase 3 EXTRACT     relevance screening, full-text fetch for the top-K, claim typing
  Phase 4 SYNTHESIZE  contradiction triage, themes, gaps
  Phase 5 AUDIT       G3 source resolution, G5 artifact structure, then publish

Gates: G1 retrieval health · G2 typed ledger · G3 sources resolve (`--live-verify`
also re-resolves every DOI through Crossref) · G4 human review for high-stakes ·
G5 artifact structure. A G5 failure leaves `<topic>.draft.md` and exits non-zero.

Claim typing and contradiction triage are lexical heuristics by default; `--adjudicate`
swaps in a model that reads the sentences and degrades back to the heuristics on any
failure. The artifact always states which path produced each section.

Stdlib only. Every writable path resolves through tools/paths.py.
"""

from __future__ import annotations
import argparse, json, os, sys, time, re, subprocess, uuid, math, urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE / "tools"))
import paths as P         # every writable location; never resolves from __file__
import retrieval as R
import adjudicate as ADJ  # optional LLM layer; degrades to heuristics without it

# ============================================================
# CONFIG
# ============================================================
# Paths are resolved through tools/paths.py, not hardcoded: as a plugin, HERE is a
# shared read-only install, and the two D:\ paths this dict used to carry existed on
# exactly one machine. See CONFIG.md for the environment variables that steer them.
CONFIG = {
    "mailto": os.environ.get("OPENALEX_MAILTO", "research@example.com"),
    "catalog": P.catalog_path(),          # None when the user has no curated catalog
    "year_from": "2022-01-01",
    "max_subagents": 3,
    "output_dir": P.output_dir(),
    "min_cites_for_high": 50,
    "min_cites_for_medium": 10,
    "relevance_threshold": 0.12,
    "top_k_fulltext": 10,
    # G1 thresholds: how broken a retrieval phase may be before the run is abandoned.
    "max_retrieval_failure_rate": 0.34,
    "min_evidence_records": 5,
    # Venue prestige is COMPUTED from the retrieved corpus at runtime (see
    # build_venue_prestige). It used to be a hand-written table of ~40 business and
    # operations journals, which silently biased every run toward one field — and had
    # grown an oncology journal entry to accommodate an off-topic paper that should
    # have been screened out instead of legitimised.
    "venue_prestige": {},
}

SUBAGENT_SCRIPT = HERE / "tools" / "subagent_retrieve.py"

# Words carrying no topical signal — excluded when deriving a topic profile.
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "have",
    "has", "had", "not", "but", "its", "their", "our", "they", "them", "these", "those",
    "can", "may", "might", "will", "would", "should", "could", "how", "what", "why",
    "when", "which", "who", "into", "over", "under", "between", "among", "using", "use",
    "used", "based", "study", "studies", "research", "paper", "article", "review",
    "results", "result", "approach", "method", "methods", "analysis", "effect",
    "effects", "role", "case", "new", "more", "most", "than", "such", "also", "via",
    "toward", "towards", "about", "across", "within", "per", "one", "two", "three",
}

CLAIM_TYPES = ("fact", "derived", "inference", "hypothesis", "recommendation", "untyped")
CONTRADICTION_TYPES = ("method", "attribution", "derivative", "genuine-empirical")

RETRIEVAL_GOALS = {
    "discovery": "Broad discovery: find seminal + recent papers for each query. Return title, abstract, year, cites, DOI, venue.",
    "targeted": "Targeted: resolve specific arXiv IDs or DOIs mentioned in queries. Return full verified records.",
    "contradiction": "Contradiction-seeking: find critiques, limitations, failed replications, boundary conditions for the main topic.",
    "gap": "Gap-filling: find papers addressing each subquestion not yet covered.",
}

# ============================================================
# UTILITIES
# ============================================================
class RetrievalGateError(RuntimeError):
    """G1 failure: the corpus is too broken to build an artifact from."""


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "research"

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def run_subagent(goal: str, context: dict, rundir: Path) -> dict:
    ctx_file = rundir / f"subagent_ctx_{uuid.uuid4().hex[:8]}.json"
    ctx_file.write_text(json.dumps(context), encoding="utf-8")
    cmd = [
        sys.executable, str(SUBAGENT_SCRIPT),
        "--goal", goal,
        "--context-file", str(ctx_file),
        "--output-dir", str(rundir),
    ]
    # Decode child output as UTF-8 explicitly. With text=True alone, Python uses the
    # locale encoding (cp1252 on this box) and any non-ASCII character in a title or
    # abstract — a minus sign, a non-breaking hyphen — raises UnicodeEncodeError in the
    # child and UnicodeDecodeError here, silently killing the pass.
    #
    # Pin the workspace explicitly: the child runs with cwd=HERE, and paths.py falls
    # back to cwd, so without this the children would cache into the plugin install
    # while the parent cached into the user's workspace — two caches, neither shared.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PEAK_WORKSPACE=str(P.workspace()), PEAK_CACHE_DIR=R.CACHE_DIR)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=300, cwd=HERE, env=env)
    if result.returncode != 0:
        return {"error": result.stderr, "goal": goal}
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"error": "invalid JSON from subagent", "raw": result.stdout, "goal": goal}

# ============================================================
# RELEVANCE SCORING & SEARCH EXPANSION
# ============================================================
def build_topic_profile(topic: str, queries: list[str]) -> dict:
    """Derive the topic vocabulary from the actual request instead of a hardcoded set.

    Returns {"terms": [single words], "phrases": [multi-word phrases]}. Phrases are kept
    separate because the old scorer stored them in a word-set and matched against
    single-token text — so every multi-word key ("prospect theory", "first offer")
    was unmatchable dead weight.
    """
    blob = " ".join([topic] + list(queries or []))
    words = [w for w in re.findall(r"\b[a-z][a-z0-9-]{2,}\b", blob.lower())
             if w not in STOPWORDS]
    phrases = []
    for src in [topic] + list(queries or []):
        toks = [w for w in re.findall(r"\b[a-z][a-z0-9-]{2,}\b", src.lower())
                if w not in STOPWORDS]
        phrases += [" ".join(toks[i:i + 2]) for i in range(len(toks) - 1)]
    return {"terms": sorted(set(words)), "phrases": sorted(set(phrases)), "expanded": []}


def expand_topic_profile(profile: dict, records: dict, top_n: int = 15) -> dict:
    """Pseudo-relevance feedback: pull additional vocabulary from the best-scoring
    records already retrieved, so the profile adapts to the literature's own wording."""
    seed = sorted(records.values(), key=lambda r: r.get("relevance_score", 0), reverse=True)[:10]
    counts = Counter()
    for r in seed:
        text = f"{r.get('title','')} {r.get('abstract','')}".lower()
        for w in re.findall(r"\b[a-z][a-z0-9-]{3,}\b", text):
            if w not in STOPWORDS and w not in profile["terms"]:
                counts[w] += 1
    profile["expanded"] = [w for w, n in counts.most_common(top_n) if n >= 2]
    return profile


def score_relevance(record: dict, profile: dict) -> float:
    """Fraction of the topic vocabulary that the record hits (0-1).

    Scored as coverage OF THE PROFILE, not of the record's own word list. The old
    formula divided by the record's unique-word count, which penalised long abstracts
    — a thorough paper scored lower than a thin one on identical topical content.
    """
    text = f"{record.get('title', '')} {record.get('abstract', '')}".lower()
    if not text.strip():
        return 0.0

    core = list(profile.get("terms", []))
    expanded = list(profile.get("expanded", []))
    phrases = list(profile.get("phrases", []))
    if not core and not phrases:
        return 0.0

    words = set(re.findall(r"\b[a-z0-9-]{3,}\b", text))
    core_hits = sum(1 for t in core if t in words)
    # Expanded terms are corpus vocabulary, not the question. They refine ranking but
    # must not be able to carry an off-topic paper over the threshold on their own,
    # so they contribute at a third of a core term's weight.
    exp_hits = sum(1 for t in expanded if t in words) / 3.0
    phrase_hits = sum(1 for p in phrases if p in text)  # phrases matched against raw text

    # Title hits weigh double — a topic word in the title is a stronger signal.
    title = (record.get("title") or "").lower()
    title_words = set(re.findall(r"\b[a-z0-9-]{3,}\b", title))
    title_hits = sum(1 for t in core if t in title_words)

    denom = max(len(core) + len(phrases), 1)
    raw = (core_hits + exp_hits + 2 * phrase_hits + title_hits) / denom
    return min(raw, 1.0)


def has_topical_anchor(record: dict, profile: dict) -> bool:
    """Does the record mention the QUESTION at all?

    A record must hit at least one term from the original topic (not the corpus-expanded
    vocabulary). Without this, generic expansion terms — 'models', 'language', 'dataset',
    'graph' — were enough to admit papers with no connection to the topic: a point-cloud
    CNN paper cleared the threshold in a legal-RAG review.
    """
    core = profile.get("terms", [])
    if not core:
        return True
    text = f"{record.get('title', '')} {record.get('abstract', '')}".lower()
    words = set(re.findall(r"\b[a-z0-9-]{3,}\b", text))
    return any(t in words for t in core)


def build_venue_prestige(records: dict) -> dict:
    """Compute a venue score from the corpus itself: venues whose papers are cited
    above the corpus median score higher. Replaces the hand-written journal table,
    which only made sense for one topic."""
    by_venue: dict[str, list[int]] = {}
    for r in records.values():
        v = (r.get("venue") or "").lower().strip()
        if v:
            by_venue.setdefault(v, []).append(r.get("cited_by_count") or 0)
    all_cites = sorted(c for cites in by_venue.values() for c in cites)
    if not all_cites:
        return {}
    median = all_cites[len(all_cites) // 2] or 1
    prestige = {}
    for v, cites in by_venue.items():
        avg = sum(cites) / len(cites)
        # map ratio-to-median into 0.5-1.0, saturating at 4x median
        prestige[v] = round(0.5 + 0.5 * min(avg / (median * 4.0), 1.0), 3)
    return prestige


def filter_relevant(records: dict, profile: dict, threshold: float = None) -> dict:
    """Keep only records above relevance threshold."""
    thresh = threshold if threshold is not None else CONFIG["relevance_threshold"]
    filtered = {}
    for k, v in records.items():
        score = score_relevance(v, profile)
        v["relevance_score"] = score
        if score >= thresh:
            filtered[k] = v
    return filtered

def expand_search_via_citations(ev: dict, rundir: Path, profile: dict) -> dict:
    """Citation chaining: find papers that cite our top papers (forward) and their references (backward)."""
    print("  [Search Expansion] Citation chaining...")
    records = ev["records"]
    # Get top 5 most cited papers
    top_papers = sorted(records.values(), key=lambda r: r.get("cited_by_count", 0), reverse=True)[:5]
    
    new_records = {}
    for paper in top_papers:
        oaid = paper.get("id")
        if not oaid:
            continue
        # Get citing papers via OpenAlex (filter=cites:OAID)
        try:
            cites_url = f"https://api.openalex.org/works?filter=cites:{oaid}&per-page=3&mailto={CONFIG['mailto']}"
            # Use retrieval's cached get
            import urllib.request, urllib.parse
            req = urllib.request.Request(cites_url, headers=R.UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            for w in data.get("results", []):
                rec = R._work_to_record(w)
                key = rec.get("doi") or rec.get("id")
                if key and key not in records and key not in new_records:
                    rec["relevance_score"] = score_relevance(rec, profile)
                    if (rec["relevance_score"] >= CONFIG["relevance_threshold"]
                            and has_topical_anchor(rec, profile)):
                        rec["screening"] = "evidence"
                        new_records[key] = rec
        except Exception:
            pass
        time.sleep(0.2)
    
    print(f"    Added {len(new_records)} citing papers")
    return new_records

def expand_search_via_authors(ev: dict, rundir: Path, profile: dict) -> dict:
    """Author search: find other papers by authors of our top papers."""
    print("  [Search Expansion] Author search...")
    records = ev["records"]
    top_papers = sorted(records.values(), key=lambda r: r.get("cited_by_count", 0), reverse=True)[:3]
    
    new_records = {}
    for paper in top_papers:
        # Use author.id, not a name search. `search=<display name>` is a full-text query
        # that matches the name anywhere in a work — including its reference list — and
        # was pulling unrelated papers into the corpus.
        for author_id in paper.get("author_ids", [])[:2]:
            if not author_id:
                continue
            try:
                search_url = f"https://api.openalex.org/works?filter=author.id:{urllib.parse.quote(author_id)}&sort=cited_by_count:desc&per-page=3&mailto={CONFIG['mailto']}"
                req = urllib.request.Request(search_url, headers=R.UA)
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
                for w in data.get("results", []):
                    rec = R._work_to_record(w)
                    key = rec.get("doi") or rec.get("id")
                    if key and key not in records and key not in new_records:
                        rec["relevance_score"] = score_relevance(rec, profile)
                        if (rec["relevance_score"] >= CONFIG["relevance_threshold"]
                                and has_topical_anchor(rec, profile)):
                            rec["screening"] = "evidence"
                            new_records[key] = rec
            except Exception:
                pass
            time.sleep(0.2)
    
    print(f"    Added {len(new_records)} author papers")
    return new_records


def expand_search_via_venues(ev: dict, rundir: Path, profile: dict, plan: dict) -> dict:
    """Venue search: find more papers in the venues this corpus already clusters in.

    Venues are read off the retrieved records (the ones carrying the most relevant
    papers), not from a hardcoded journal list — that list was specific to one topic
    and quietly steered every other topic into business/operations journals.
    """
    print("  [Search Expansion] Venue search...")
    venue_weight = Counter()
    for r in ev["records"].values():
        v = (r.get("venue") or "").strip()
        if v:
            venue_weight[v] += r.get("relevance_score", 0)
    key_venues = [v for v, _ in venue_weight.most_common(8)]
    if not key_venues:
        print("    No venues in corpus yet; skipping venue expansion")
        return {}

    # Search term for the in-venue query comes from the topic, not a fixed word.
    stem = (profile.get("terms") or [plan.get("topic", "")])[0]

    new_records = {}
    for venue in key_venues:
        try:
            search_url = f"https://api.openalex.org/works?filter=source.display_name:{urllib.parse.quote(venue)},title_and_abstract.search:{urllib.parse.quote(stem)}&per-page=3&mailto={CONFIG['mailto']}"
            req = urllib.request.Request(search_url, headers=R.UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            for w in data.get("results", []):
                rec = R._work_to_record(w)
                key = rec.get("doi") or rec.get("id")
                if key and key not in ev["records"] and key not in new_records:
                    rec["relevance_score"] = score_relevance(rec, profile)
                    if (rec["relevance_score"] >= CONFIG["relevance_threshold"]
                            and has_topical_anchor(rec, profile)):
                        rec["screening"] = "evidence"
                        new_records[key] = rec
        except Exception:
            pass
        time.sleep(0.2)
    
    print(f"    Added {len(new_records)} venue papers")
    return new_records

# ============================================================
# PHASE 1 — PLAN
# ============================================================
def derive_queries(topic: str) -> list[str]:
    """Build discovery queries from the topic when the caller supplies none.

    Replaces a hardcoded three-query B2B list that ran regardless of --topic.
    """
    toks = [w for w in re.findall(r"\b[a-z][a-z0-9-]{2,}\b", topic.lower())
            if w not in STOPWORDS]
    if not toks:
        return [topic]
    queries = [topic]
    if len(toks) >= 4:
        mid = len(toks) // 2
        queries.append(" ".join(toks[:mid]))
        queries.append(" ".join(toks[mid:]))
    elif len(toks) > 1:
        queries.append(" ".join(toks[:2]))
    return list(dict.fromkeys(q for q in queries if q.strip()))


def phase1_plan(topic: str, queries: list, archetype: str, rundir: Path) -> dict:
    queries = queries or derive_queries(topic)
    plan = {
        "topic": topic,
        "archetype": archetype,
        "objective": f"Evidence-first review: {topic}",
        "subquestions": [
            {"id": "bg", "label": "background", "prompt": "Foundational theories, classic papers, key constructs"},
            {"id": "ev", "label": "current_evidence", "prompt": "Recent empirical studies, meta-analyses, replications (2022+)"},
            {"id": "alt", "label": "alternatives", "prompt": "Competing frameworks, boundary conditions, failed replications"},
            {"id": "risk", "label": "risks", "prompt": "Methodological limits, publication bias, ecological validity"},
        ],
        "queries": queries,
        "topic_profile": build_topic_profile(topic, queries),
        "scope": {
            "year_from": CONFIG["year_from"],
            "source_pref": "primary>derivative",
            "max_per_query": 5,
        },
        "created": now_iso(),
    }
    (rundir / "PLAN.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan

# ============================================================
# PHASE 2 — RETRIEVE (4-pass + expansion)
# ============================================================
def build_subagent_contexts(plan: dict) -> list[dict]:
    contexts = []
    queries = plan["queries"]
    subqs = [sq["label"] for sq in plan["subquestions"]]
    scope = plan["scope"]

    for q in queries:
        contexts.append({
            "pass": "discovery",
            "query": q,
            "year_from": scope["year_from"],
            "per": scope["max_per_query"],
        })

    for token in queries:
        m = re.search(r"(\d{4}\.\d{4,5})", token)
        if m:
            contexts.append({
                "pass": "targeted",
                "arxiv_id": m.group(1),
            })

    # Contradiction pass: seek dissent about the topic itself, not about its first word.
    profile_terms = plan.get("topic_profile", {}).get("terms", [])
    main_stem = " ".join(profile_terms[:3]) if profile_terms else plan.get("topic", "")
    for stem in [f"{main_stem} limitation", f"{main_stem} critique",
                 f"{main_stem} failed replication", f"criticism of {main_stem}"]:
        contexts.append({
            "pass": "contradiction",
            "query": stem,
            "year_from": scope["year_from"],
            "per": 2,
        })

    # Gap pass: the subquestion label alone ("background", "risks") is a meaningless
    # global search — it returned unrelated papers from every field. Compose it with
    # the topic so the query is actually about this review.
    gap_hints = {
        "background": "theory foundations",
        "current_evidence": "empirical evidence",
        "alternatives": "alternative approaches comparison",
        "risks": "limitations validity bias",
    }
    topic_stem = " ".join(plan.get("topic_profile", {}).get("terms", [])[:3]) or plan["topic"]
    for sq in subqs:
        contexts.append({
            "pass": "gap",
            "subquestion": sq,
            "query": f"{topic_stem} {gap_hints.get(sq, sq.replace('_', ' '))}",
            "year_from": scope["year_from"],
            "per": 2,
        })

    return contexts

def phase2_retrieve(plan: dict, rundir: Path) -> dict:
    print(f"  [Phase 2] Building retrieval tasks...")

    # tools/subagent_retrieve.py is now checked-in source, not generated at runtime.
    # It used to be re-emitted from a string literal here on every run, which meant a
    # second copy of the same code to keep in sync — and a write into what is, once
    # installed as a plugin, a read-only directory.
    if not SUBAGENT_SCRIPT.exists():
        raise RetrievalGateError(f"missing retrieval worker: {SUBAGENT_SCRIPT}")

    contexts = build_subagent_contexts(plan)
    all_records = {}
    passes_done = set()
    failures = []

    with ThreadPoolExecutor(max_workers=CONFIG["max_subagents"]) as executor:
        future_to_ctx = {
            executor.submit(run_subagent, RETRIEVAL_GOALS[ctx["pass"]], ctx, rundir): ctx
            for ctx in contexts
        }
        for future in as_completed(future_to_ctx):
            ctx = future_to_ctx[future]
            label = ctx.get("query", ctx.get("arxiv_id", ctx.get("subquestion", "")))
            try:
                result = future.result()
                if "error" in result:
                    err = str(result["error"]).strip().splitlines()[-1][:160]
                    print(f"    [{ctx['pass']}] FAILED {label!r}: {err}")
                    failures.append({"pass": ctx["pass"], "query": label, "error": err})
                    continue
                passes_done.add(ctx["pass"])
                for k, v in result.get("records", {}).items():
                    all_records[k] = v
                print(f"    [{ctx['pass']}] {label} -> {len(result.get('records', {}))}")
            except Exception as e:
                print(f"    [{ctx['pass']}] EXCEPTION {label!r}: {e}")
                failures.append({"pass": ctx["pass"], "query": label, "error": repr(e)})

    # G1: a run whose retrieval mostly failed must not proceed to produce an artifact.
    # Previously every failure was printed and skipped, and the pipeline published
    # whatever stragglers survived — a run with 4 of 4 discovery passes crashed still
    # emitted a finished-looking report.
    fail_rate = len(failures) / max(len(contexts), 1)
    (rundir / "RETRIEVAL_FAILURES.json").write_text(
        json.dumps(failures, indent=2), encoding="utf-8")
    if failures:
        print(f"  [Phase 2] {len(failures)}/{len(contexts)} retrieval tasks failed "
              f"({fail_rate:.0%}) — see RETRIEVAL_FAILURES.json")
    if fail_rate > CONFIG["max_retrieval_failure_rate"]:
        raise RetrievalGateError(
            f"G1 FAILED: {len(failures)}/{len(contexts)} retrieval tasks failed "
            f"({fail_rate:.0%} > {CONFIG['max_retrieval_failure_rate']:.0%}). "
            f"First error: {failures[0]['error']}")
    if "discovery" not in passes_done:
        raise RetrievalGateError(
            "G1 FAILED: no discovery pass succeeded — there is no corpus to review.")

    # Relevance scoring, then vocabulary expansion from the best hits, then rescore.
    profile = plan["topic_profile"]
    print(f"  [Phase 2] Relevance scoring (threshold={CONFIG['relevance_threshold']})...")
    for k, v in all_records.items():
        v["relevance_score"] = score_relevance(v, profile)
    profile = expand_topic_profile(profile, all_records)
    if profile["expanded"]:
        print(f"  [Phase 2] Profile expanded with: {', '.join(profile['expanded'][:10])}")
    for k, v in all_records.items():
        v["relevance_score"] = score_relevance(v, profile)

    # POST-RETRIEVAL FILTER. The citation and venue escape hatches are gone: a highly
    # cited paper from a prestigious journal about an unrelated field is still an
    # unrelated paper, and those exemptions are how an oncology review ended up cited
    # in a wholesale-negotiation report. Off-topic records are kept as DISCOVERY-ONLY
    # (recorded, not citable) rather than silently deleted.
    print("  [Phase 2] Post-retrieval filtering...")
    filtered_records, discovery_only = {}, {}
    for k, v in all_records.items():
        rel = v.get("relevance_score", 0)
        anchored = has_topical_anchor(v, profile)
        if rel >= CONFIG["relevance_threshold"] and anchored:
            v["screening"] = "evidence"
            filtered_records[k] = v
        else:
            v["screening"] = "discovery_only"
            v["screening_reason"] = ("no topical anchor (mentions no term from the question)"
                                     if not anchored
                                     else f"relevance {rel:.3f} < {CONFIG['relevance_threshold']}")
            discovery_only[k] = v
            print(f"    Discovery-only: {v.get('title', '')[:58]} — {v['screening_reason']}")

    all_records = dict(sorted(filtered_records.items(), key=lambda x: x[1].get("relevance_score", 0), reverse=True))
    print(f"  [Phase 2] Evidence set: {len(all_records)} | discovery-only: {len(discovery_only)}")

    # Venue prestige is derived from the screened corpus, then used by compute_strength.
    CONFIG["venue_prestige"] = build_venue_prestige(all_records)

    # Search expansion: citation chaining + author search + venue search
    new_cites = expand_search_via_citations({"records": all_records}, rundir, profile)
    all_records.update(new_cites)

    new_authors = expand_search_via_authors({"records": all_records}, rundir, profile)
    all_records.update(new_authors)

    new_venues = expand_search_via_venues({"records": all_records}, rundir, profile, plan)
    all_records.update(new_venues)

    all_records = dict(sorted(all_records.items(), key=lambda x: x[1].get("relevance_score", 0), reverse=True))
    CONFIG["venue_prestige"] = build_venue_prestige(all_records)

    if len(all_records) < CONFIG["min_evidence_records"]:
        raise RetrievalGateError(
            f"G1 FAILED: only {len(all_records)} record(s) survived screening "
            f"(minimum {CONFIG['min_evidence_records']}). Widen the queries, lower "
            f"relevance_threshold, or broaden --year before reporting on this topic.")

    ev = {
        "records": all_records,
        "discovery_only": discovery_only,
        "retrieval_failures": failures,
        "topic_profile": profile,
        "venue_prestige": CONFIG["venue_prestige"],
        "passes": sorted(passes_done) + ["citation_chaining", "author_search", "venue_search"],
        "retrieved_at": now_iso(),
    }
    (rundir / "EVIDENCE.json").write_text(json.dumps(ev, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [Phase 2] Total unique records after expansion+filter: {len(all_records)}")
    return ev

# ============================================================
# PHASE 3 — EXTRACT: LLM-Assisted Claim Typing + Full-Text
# ============================================================
def fetch_fulltext_top_k(ev: dict, rundir: Path) -> dict:
    """Fetch full text via Jina for top-K most cited relevant papers.
    Uses Unpaywall to find PDF URLs first, then Jina for extraction.
    Falls back to CORE API and Semantic Scholar for PDF URLs."""
    print(f"  [Phase 3] Fetching full text for top {CONFIG['top_k_fulltext']} papers...")
    
    # Sort by cites desc, then relevance
    sorted_papers = sorted(
        ev["records"].values(),
        key=lambda r: (r.get("cited_by_count", 0), r.get("relevance_score", 0)),
        reverse=True
    )[:CONFIG["top_k_fulltext"]]

    fulltexts = {}
    for paper in sorted_papers:
        # Try Unpaywall first for PDF URL
        pdf_url = get_pdf_url_via_unpaywall(paper.get("doi"))
        
        # Fallback 1: CORE API for PDF
        if not pdf_url:
            pdf_url = get_pdf_url_via_core(paper.get("doi"), paper.get("title"))
        
        # Fallback 2: Semantic Scholar
        if not pdf_url:
            pdf_url = get_pdf_url_via_semantic_scholar(paper.get("doi"), paper.get("title"))
        
        if pdf_url:
            fetch_url = pdf_url
            print(f"    [PDF] {paper.get('title', '')[:60]}")
        else:
            # Fallback to oa_url or doi
            oa_url = paper.get("oa_url") or paper.get("doi")
            if not oa_url:
                continue
            fetch_url = oa_url
            print(f"    [HTML] {paper.get('title', '')[:60]}")
        
        try:
            txt = R.jina_fetch(fetch_url)
            txt = clean_jina_text(txt)
            if not txt:
                print(f"    Fulltext rejected (bot wall / no article text): {paper.get('title','')[:60]}")
                continue
            fulltexts[paper.get("id") or paper.get("doi")] = txt[:80000]
            print(f"    Fulltext: {paper.get('title', '')[:60]} ({len(txt)} chars)")
        except Exception as e:
            print(f"    Fulltext failed for {paper.get('title', '')[:60]}: {e}")
        time.sleep(0.3)

    (rundir / "FULLTEXTS.json").write_text(json.dumps(fulltexts, ensure_ascii=False), encoding="utf-8")
    return fulltexts


def get_pdf_url_via_unpaywall(doi: str) -> str | None:
    """Query Unpaywall API for open-access PDF URL."""
    if not doi:
        return None
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={CONFIG['mailto']}"
        req = urllib.request.Request(url, headers=R.UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        # Prefer PDF URL, then any fulltext URL
        if data.get("best_oa_location", {}).get("url_for_pdf"):
            return data["best_oa_location"]["url_for_pdf"]
        if data.get("best_oa_location", {}).get("url"):
            return data["best_oa_location"]["url"]
        # Check all OA locations
        for loc in data.get("oa_locations", []):
            if loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
            if loc.get("url"):
                return loc["url"]
    except Exception:
        pass
    return None


def get_pdf_url_via_core(doi: str, title: str) -> str | None:
    """Query CORE API for open-access PDF URL."""
    if not doi and not title:
        return None
    try:
        query = doi if doi else title
        url = f"https://api.core.ac.uk/v3/search/works?q={urllib.parse.quote(query)}&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "peak-research-toolkit/2.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        results = data.get("results", [])
        if results:
            work = results[0]
            if work.get("downloadUrl"):
                return work["downloadUrl"]
            if work.get("pdfUrl"):
                return work["pdfUrl"]
    except Exception:
        pass
    return None


def get_pdf_url_via_semantic_scholar(doi: str, title: str) -> str | None:
    """Query Semantic Scholar for open-access PDF URL."""
    if not doi and not title:
        return None
    try:
        if doi:
            url = f"https://api.semanticscholar.org/graph/v1/paper/{doi}?fields=openAccessPdf,url,title"
        else:
            url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={urllib.parse.quote(title)}&fields=openAccessPdf,url,title&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "peak-research-toolkit/2.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        # If searching by title, get first result
        if "data" in data and data["data"]:
            paper = data["data"][0]
        else:
            paper = data
        if paper.get("openAccessPdf", {}).get("url"):
            return paper["openAccessPdf"]["url"]
    except Exception:
        pass
    return None


# Boilerplate lines to drop. Matched per-line WITHOUT DOTALL: the old version ran
# patterns like r"(?i)timestamp:.*" with re.DOTALL, where the greedy `.*` swallows
# every remaining character in the document — one "Timestamp:" in a bot-check page
# truncated the entire fulltext. Others (r"(?i)doi", r"(?i)copyright") deleted
# substrings mid-sentence and corrupted the prose that claims are extracted from.
BOILERPLATE_LINE_PATTERNS = [
    r"^\s*(we use cookies|cookie (policy|settings|preferences))",
    r"^\s*skip to (main )?content\s*$",
    r"^\s*navigation (menu|toggle)\s*$",
    r"^\s*(privacy policy|terms of (use|service)|copyright|all rights reserved)\b",
    r"^\s*(are you a robot|please confirm you are a human)",
    r"^\s*(reference number|ip address|user agent|timestamp)\s*:",
    r"^\s*(cloudflare|captcha|security verification)\b",
    r"^\s*(creative commons|the doi handbook|iso 26324|governing board|status page)\b",
    r"^\s*this page is displayed while",
    r"^\s*website uses a security service",
    r"^\s*(text and data mining|ai training)\b",
]
_BOILERPLATE_RE = re.compile("|".join(BOILERPLATE_LINE_PATTERNS), re.I)
# A page that is mostly bot-check text is not fulltext; reject rather than clean.
BOT_WALL_MARKERS = ["are you a robot", "security verification", "enable javascript and cookies",
                    "please confirm you are a human", "access denied"]


def clean_jina_text(txt: str) -> str:
    """Drop boilerplate LINES from fetched fulltext. Line-scoped and non-greedy.

    Returns "" if the page looks like a bot wall rather than an article, so the caller
    records a retrieval failure instead of extracting claims from a CAPTCHA page.
    """
    head = txt[:3000].lower()
    if any(m in head for m in BOT_WALL_MARKERS) and len(txt) < 6000:
        return ""

    kept = [ln for ln in txt.splitlines() if not _BOILERPLATE_RE.search(ln)]
    txt = "\n".join(kept)
    txt = re.sub(r"\[\d+\]", "", txt)          # inline citation markers
    txt = re.sub(r"\n{3,}", "\n\n", txt)       # excessive blank lines
    txt = re.sub(r"[ \t]{2,}", " ", txt)       # runs of spaces, newlines preserved
    return txt.strip()

def classify_claim_type(claim_text: str, context: str = "") -> tuple[str, str | None]:
    """Heuristic claim typing. Returns (type, matched_cue).

    Cue-based typing is weak evidence — it reads surface wording, not argument
    structure. The matched cue is returned so the ledger can show its own reasoning
    and a reader can overrule it. Sentences matching no cue are typed 'untyped'
    rather than silently bucketed."""
    lower = claim_text.lower()
    
    # Fact: empirical finding with specific result
    fact_cues = [
        "we find", "results show", "data indicate", "study shows", "demonstrate that",
        "reveal that", "observed that", "found that", "showed that", "indicated that",
        "significant", "p <", "correlation", "effect size", "increased", "decreased",
        "higher than", "lower than", "predicted", "mediated", "moderated",
        "we found", "results indicate", "empirically", "statistically significant",
        "p-value", "confidence interval", "experiment", "trial", "observed",
        "measured", "documented", "established", "proven", "demonstrated", "confirmed",
        "randomized controlled", "meta-analysis", "systematic review", "n =", "sample",
        "participants", "subjects", "data show", "analysis reveals",
        "significant effect", "significant difference", "significant relationship",
        "mean", "median", "standard deviation", "odds ratio", "hazard ratio",
        "significant at", "significantly"
    ]
    
    # Inference: suggestive but not definitive
    inf_cues = [
        "suggest", "may indicate", "could imply", "consistent with", "points to",
        "likely", "probably", "possibly", "appears to", "seems to", "evidence for",
        "supports the idea", "in line with", "implies", "indicates", "hint",
        "interpretation", "we interpret", "may be", "could be", "might be",
        "is consistent", "is suggestive of", "lends support", "compatible with"
    ]
    
    # Hypothesis: proposed mechanism or prediction
    hyp_cues = [
        "we propose", "we argue", "framework suggests", "model predicts", "hypothesize",
        "we predict", "we expect", "theoretical", "mechanism", "mediator", "moderator",
        "boundary condition", "future research should", "we hypothesize", "postulate",
        "theoretical prediction", "predicts that", "would predict", "hypothesis",
        "proposed mechanism", "underlying mechanism"
    ]
    
    # Recommendation: actionable advice
    rec_cues = [
        "recommend", "should", "ought to", "implication for practice", "policy implication",
        "managers should", "practitioners", "actionable", "guideline", "best practice",
        "we advise", "advised to", "practical implication", "should consider",
        "it is recommended", "recommendation", "take action", "implement"
    ]
    
    # Derived: synthesis, definition, framework (default)
    derived_cues = [
        "framework", "model", "theory", "construct", "definition", "conceptual",
        "literature review", "synthesis", "overview", "perspective", "review",
        "we define", "we conceptualize", "refers to", "is defined as"
    ]

    # Return (type, matched_cue). Order matters: a hedge ("suggests") outranks a
    # statistical word, because "results suggest a significant effect" is an inference.
    for label, cues in (("inference", inf_cues), ("hypothesis", hyp_cues),
                        ("recommendation", rec_cues), ("fact", fact_cues),
                        ("derived", derived_cues)):
        for c in cues:
            if c in lower:
                return label, c
    # No cue matched. Previously this fell through to "derived", which put 92% of all
    # sentences into a type they were never evidenced to be. "untyped" is the truth,
    # and it makes the unclassified share visible in the ledger stats.
    return "untyped", None


def compute_strength(cites: int, relevance: float, year: int, venue: str) -> str:
    """Composite strength: cites + recency + relevance + venue prestige."""
    from datetime import datetime
    current_year = datetime.now().year
    
    # Citation score (log scale, capped)
    cite_score = min(math.log10(max(cites, 1) + 1) / 2.0, 1.0)  # 0-1
    
    # Recency score (1.0 for current year, decaying)
    age = max(0, current_year - (year or current_year))
    recency_score = max(0.0, 1.0 - age * 0.08)  # ~12 years to 0
    
    # Relevance score (already 0-1)
    rel_score = min(relevance * 3, 1.0)  # boost relevance
    
    # Venue prestige (0.5-1.0)
    venue_lower = (venue or "").lower().strip()
    venue_score = CONFIG["venue_prestige"].get(venue_lower, 0.6)
    
    # Weighted composite
    composite = (
        0.35 * cite_score +
        0.20 * recency_score +
        0.25 * rel_score +
        0.20 * venue_score
    )
    
    if composite >= 0.65:
        return "high"
    elif composite >= 0.40:
        return "medium"
    else:
        return "low"

# A sentence must contain an assertive verb to be a claim candidate. Without this,
# titles and fragments ("Joint forward contract negotiation: The role of B2B
# procurement platforms.") were emitted as typed claims.
ASSERTIVE_VERB_RE = re.compile(
    r"\b(is|are|was|were|has|have|had|show|shows|showed|find|finds|found|report|reports|"
    r"reported|demonstrate|demonstrates|demonstrated|indicate|indicates|indicated|"
    r"suggest|suggests|suggested|reveal|reveals|revealed|increase[sd]?|decrease[sd]?|"
    r"reduce[sd]?|improve[sd]?|predict[sd]?|affect[sd]?|cause[sd]?|lead[s]?|correlate[sd]?|"
    r"differ[s]?|exceed[s]?|remain[s]?|appear[s]?|require[s]?|enable[s]?|result[sd]?)\b", re.I)

# Section headers and reference-list debris that survive fulltext extraction.
NON_CLAIM_RE = re.compile(
    r"^\s*(abstract|introduction|methods?|results?|discussion|conclusions?|references|"
    r"acknowledg|appendix|keywords|figure \d|table \d|supplementary)\b", re.I)


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\{[\w,.]+\}@")

# Paper furniture: true sentences that assert nothing about the subject. These are
# near-identical across papers, so they also produced false "corroborated by N sources"
# hits — the only cross-source statements in one run were two code-availability lines.
BOILERPLATE_CLAIM_RE = re.compile(
    r"\b(code[s]?|data|datasets?|materials?|supplementary)\b.{0,60}?\b"
    r"(are|is|will be|can be)\s+(publicly\s+|freely\s+)?available"
    r"|available at\s*:?\s*https?://"
    r"|\bthis work (was|is) (supported|funded)"
    r"|\bfunded by\b|\bgrant no\b|\bconflicts? of interest\b"
    r"|\bthe authors declare\b|\ball authors (read|approved)\b"
    r"|\bcorresponding author\b|\bcopyright\b|\blicensed under\b"
    r"|\bwe thank\b|\backnowledge(s|ment)\b"
    r"|\b(see|cf\.?)\s+(figure|table|section|appendix)\s*\d"
    r"|\bthe (remainder|rest) of th(is|e) paper\b"
    r"|\bis organized as follows\b", re.I)


def sanitize_claim(sent: str) -> str:
    """Flatten extracted text so it cannot restructure the artifact.

    Fulltext arrives as markdown: a sentence may span lines and carry '#' or '|'
    characters. Emitted verbatim, those became phantom headings and broken table rows
    in the deliverable — G5 flagged the author block of a fetched PDF as an 'empty
    section' because it had been rendered as one.
    """
    s = re.sub(r"\s+", " ", sent).strip()
    s = re.sub(r"^[#>*\-\s]+", "", s)   # leading markdown structure
    s = s.replace("|", "/").replace("`", "'")
    return s.strip()


def is_claim_candidate(sent: str, title: str) -> bool:
    """Gate a sentence before it is called a claim."""
    s = sanitize_claim(sent)
    if len(s) < 60 or len(s) > 800:
        return False
    if NON_CLAIM_RE.match(s):
        return False
    if BOILERPLATE_CLAIM_RE.search(s):
        return False
    if not ASSERTIVE_VERB_RE.search(s):
        return False
    # The paper's own title is not a claim it makes.
    if title and R.title_overlap(s, title) > 0.75:
        return False
    # Needs real prose, not a citation string or author list.
    if len(re.findall(r"\b[a-z]{3,}\b", s.lower())) < 8:
        return False
    # Mostly-digits lines are tables/references.
    if sum(c.isdigit() for c in s) > len(s) * 0.25:
        return False
    # Author/affiliation blocks from PDF headers: emails, or a high density of
    # Capitalised tokens with no sentence-like lowercase run.
    if EMAIL_RE.search(s):
        return False
    tokens = s.split()
    caps = sum(1 for t in tokens if t[:1].isupper())
    if tokens and caps / len(tokens) > 0.5:
        return False
    return True


def extract_claims_from_record(record: dict, fulltext: str = "") -> list[dict]:
    """Extract claim CANDIDATES — assertive sentences — from fulltext, else abstract.

    These are excerpts selected by heuristic, not claims an analyst has adjudicated.
    Every row carries `verification: unverified` so the deliverable cannot present
    them as vetted findings.
    """
    claims = []
    title = record.get("title", "")
    abstract = record.get("abstract", "")
    year = record.get("year")
    venue = record.get("venue", "")
    doi = record.get("doi", "")
    cites = record.get("cited_by_count", 0)
    relevance = record.get("relevance_score", 0)

    text_source = fulltext if fulltext else abstract
    if not text_source:
        return claims

    provenance = "fulltext" if fulltext else "abstract"
    sentences = re.split(r"(?<=[.!?])\s+", text_source)

    for sent in sentences:
        if not is_claim_candidate(sent, title):
            continue
        sent = sanitize_claim(sent)

        ctype, cue = classify_claim_type(sent)
        strength = compute_strength(cites, relevance, year, venue)

        claims.append({
            "claim": sent[:600],
            "type": ctype,
            "type_cue": cue,
            "type_confidence": "low" if cue is None else "heuristic",
            "source_id": record.get("id") or record.get("doi") or record.get("title", "")[:50],
            "source_title": title,
            "source_year": year,
            "source_venue": venue,
            "source_doi": doi,
            "source_cites": cites,
            "source_relevance": round(relevance, 3),
            "strength": strength,
            "provenance": provenance,
            "verification": "unverified",
            "caveat": f"Candidate sentence auto-extracted from {provenance}; "
                      f"not adjudicated — read in context before citing.",
        })

    return claims[:10]  # Cap per paper

def phase3_extract(ev: dict, rundir: Path, adjudicate: bool = False,
                   topic: str = "") -> dict:
    print("  [Phase 3] Fetching fulltext for top papers...")
    fulltexts = fetch_fulltext_top_k(ev, rundir)

    print("  [Phase 3] Extracting claims with improved typing...")
    ledger = []
    evidence_matrix = []

    for rec_id, record in ev["records"].items():
        fulltext = fulltexts.get(record.get("id") or record.get("doi"), "")
        claims = extract_claims_from_record(record, fulltext)
        for c in claims:
            ledger.append(c)
            evidence_matrix.append({
                "claim": c["claim"],
                "type": c["type"],
                # The old "evidence" column restated the source's own metadata, so every
                # row read "claim X is supported by the paper X was copied from". The
                # matrix now reports WHERE the sentence came from and how confident the
                # typing is — no pretence of independent corroboration.
                "extracted_from": f"{c['provenance']} of {c['source_title']} ({c['source_year']})",
                "source": c["source_doi"] or c["source_id"],
                "source_signal": f"{c['source_venue'] or 'no venue'}, {c['source_cites']} cites, rel={c['source_relevance']}",
                "corroboration": "single-source",  # upgraded below where sources agree
                "strength": c["strength"],
                "type_confidence": c["type_confidence"],
                "caveat": c["caveat"],
            })

    ledger = dedupe_claims(ledger)

    # Optional: replace cue matching with an LLM that reads each sentence.
    # Returns None when unavailable, in which case the heuristic types stand.
    adjudicated = ADJ.type_claims(ledger, topic) if adjudicate else None
    if adjudicated is not None:
        before = len(adjudicated)
        ledger = [c for c in adjudicated if c.get("substantive", True)]
        kept_types = Counter(c["type"] for c in ledger)
        print(f"  [Phase 3] LLM adjudication: {before - len(ledger)} non-substantive "
              f"dropped, {len(ledger)} typed {dict(kept_types)}")
        # Rebuild the matrix so it reflects the adjudicated ledger, not the raw one.
        claim_to_row = {(r["claim"], r["source"]): r for r in evidence_matrix}
        evidence_matrix = []
        for c in ledger:
            row = claim_to_row.get((c["claim"], c["source_doi"] or c["source_id"]))
            if row:
                row["type"] = c["type"]
                row["type_confidence"] = c["type_confidence"]
                evidence_matrix.append(row)

    evidence_matrix = mark_corroboration(evidence_matrix)

    typed = sum(1 for c in ledger if c["type"] != "untyped")
    result = {
        "claim_ledger": ledger,
        "evidence_matrix": evidence_matrix,
        "extracted_at": now_iso(),
        "stats": {
            "total_claims": len(ledger),
            "typed": typed,
            "untyped_share": round(1 - typed / max(len(ledger), 1), 3),
            "by_type": {t: sum(1 for c in ledger if c["type"] == t) for t in CLAIM_TYPES},
            "by_strength": {s: sum(1 for c in ledger if c["strength"] == s) for s in ("high", "medium", "low")},
            "by_provenance": {p: sum(1 for c in ledger if c["provenance"] == p) for p in ("fulltext", "abstract")},
            "multi_source_claims": sum(1 for r in evidence_matrix if r["corroboration"] != "single-source"),
        },
    }
    (rundir / "CLAIM_LEDGER.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (rundir / "EVIDENCE_MATRIX.json").write_text(json.dumps(evidence_matrix, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [Phase 3] Extracted {len(ledger)} claims: {result['stats']['by_type']} | strength: {result['stats']['by_strength']}")
    return result

# ============================================================
# PHASE 4 — SYNTHESIZE: Better Contradictions + Themes
# ============================================================
def mark_corroboration(matrix: list[dict]) -> list[dict]:
    """Flag matrix rows whose claim is echoed by a DIFFERENT source.

    This is the only genuine cross-source signal the pipeline can compute without an
    analyst, so it is worth surfacing: a sentence that two independent papers state
    is a different object from one that appears once.
    """
    if len(matrix) < 2:
        return matrix
    embeddings = simple_embed([r["claim"] for r in matrix])
    for i, row in enumerate(matrix):
        agreeing = set()
        for j, other in enumerate(matrix):
            if i == j or other["source"] == row["source"]:
                continue
            if cosine_sim(embeddings[i], embeddings[j]) >= 0.5:
                agreeing.add(other["source"])
        if agreeing:
            row["corroboration"] = f"{len(agreeing) + 1} sources"
    return matrix


def dedupe_claims(claims: list[dict]) -> list[dict]:
    """Remove near-duplicate claims (same source + similar text)."""
    seen = set()
    unique = []
    for c in claims:
        key = (c["source_id"], c["claim"][:100])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique

POSITIVE_CUES = [
    "improve", "improves", "improved", "increase", "increases", "increased", "positive",
    "benefit", "benefits", "enhance", "enhances", "enhanced", "effective", "gain",
    "success", "successful", "better", "stronger", "higher", "boost", "boosts", "raise",
    "amplify", "amplifies", "optimal", "favorable", "advantage", "profit", "win",
    "outperform", "outperforms", "facilitate", "facilitates", "promote", "promotes",
    "supports", "strengthens", "greater",
]
NEGATIVE_CUES = [
    "reduce", "reduces", "reduced", "decrease", "decreases", "decreased", "negative",
    "harm", "harms", "fail", "fails", "failed", "ineffective", "weaken", "weakens",
    "lower", "lowers", "worse", "diminish", "diminishes", "undermine", "undermines",
    "hinder", "hinders", "impair", "impairs", "damage", "damages", "no effect",
    "not significant", "no significant", "null effect", "fails to", "does not",
    "did not", "cannot", "unable to", "insufficient", "smaller", "weaker",
]
# Negators that flip the polarity of the cue they precede.
NEGATORS = ["not ", "no ", "never ", "fails to ", "failed to ", "does not ", "did not ",
            "cannot ", "rather than ", "instead of ", "contrary to "]


def _polarity(text: str) -> tuple[int, int]:
    """Count directional cues actually present in `text`, honouring nearby negators.

    The previous implementation was `sum(1 for w in [<list>])`, which counts the
    length of the cue list and never inspects the text — so every claim scored
    identically and no contradiction could ever be emitted.
    """
    t = " " + text.lower() + " "
    pos = neg = 0
    for cue in POSITIVE_CUES:
        for m in re.finditer(r"\b%s\b" % re.escape(cue), t):
            window = t[max(0, m.start() - 24):m.start()]
            if any(n in window for n in NEGATORS):
                neg += 1
            else:
                pos += 1
    for cue in NEGATIVE_CUES:
        for m in re.finditer(r"\b%s\b" % re.escape(cue), t):
            window = t[max(0, m.start() - 24):m.start()]
            if any(n in window for n in NEGATORS):
                pos += 1
            else:
                neg += 1
    return pos, neg


def detect_contradictions(ledger: list[dict], topic_terms: list[str] | None = None) -> list[dict]:
    """Find claim pairs that point in opposite directions on the same subject.

    Grouping terms come from the topic profile, not a hardcoded domain vocabulary.
    A candidate is only emitted when the two sides come from DIFFERENT papers and
    share enough lexical content to plausibly be about the same thing. Output is
    labelled `candidate` — direction-of-effect disagreement detected lexically is a
    prompt for human triage, not an established empirical conflict.
    """
    ledger = dedupe_claims(ledger)
    terms = [t for t in (topic_terms or []) if len(t) >= 4]
    if not terms:
        return []

    claims_by_topic: dict[str, list[dict]] = {}
    for c in ledger:
        claim_text = c["claim"].lower()
        for kw in terms:
            if re.search(r"\b%s" % re.escape(kw), claim_text):
                claims_by_topic.setdefault(kw, []).append(c)

    contradictions = []
    seen_pairs = set()
    for topic, claims in claims_by_topic.items():
        if len(claims) < 2:
            continue

        embeddings = simple_embed([c["claim"] for c in claims])
        clusters = cluster_claims(claims, embeddings, threshold=0.35)

        for cluster in clusters:
            if len(cluster) < 2:
                continue

            positive, negative = [], []
            for c in cluster:
                pos, neg = _polarity(c["claim"])
                if pos > neg:
                    positive.append(c)
                elif neg > pos:
                    negative.append(c)
                # equal (incl. 0/0) => no direction, excluded

            if not (positive and negative):
                continue

            sources_for = {c["source_id"] for c in positive}
            sources_against = {c["source_id"] for c in negative}
            # Require genuinely different papers on each side, not just unequal sets.
            if not (sources_for - sources_against) or not (sources_against - sources_for):
                continue

            pair_key = (topic, frozenset(sources_for), frozenset(sources_against))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            contradictions.append({
                "topic": topic,
                "cluster_size": len(cluster),
                "type": "candidate",
                "status": "unresolved — requires human triage",
                "claims_for": [c["claim"][:250] for c in positive[:3]],
                "claims_against": [c["claim"][:250] for c in negative[:3]],
                "sources_for": [f"{c['source_title']} ({c['source_year']})" for c in positive[:3]],
                "sources_against": [f"{c['source_title']} ({c['source_year']})" for c in negative[:3]],
                "resolution": "Check population, metric, timeframe, context, operationalization "
                              "before calling this a genuine empirical conflict "
                              "(METHOD step 13: method/definition mismatch is not disagreement).",
            })

    contradictions.sort(key=lambda c: c["cluster_size"], reverse=True)
    return contradictions[:25]


def simple_embed(texts: list[str]) -> list[dict]:
    """Simple TF-IDF style embeddings for claims."""
    # Build vocabulary
    all_words = []
    for t in texts:
        words = re.findall(r"\b[a-z]{3,}\b", t.lower())
        all_words.extend(words)
    
    # TF-IDF style: weight by inverse doc frequency
    word_counts = Counter(all_words)
    total_docs = len(texts)
    
    embeddings = []
    for t in texts:
        words = re.findall(r"\b[a-z]{3,}\b", t.lower())
        tf = Counter(words)
        vec = {}
        for w, count in tf.items():
            idf = math.log(total_docs / (1 + sum(1 for tw in texts if w in tw.lower())))
            vec[w] = count * idf
        embeddings.append(vec)
    
    return embeddings


def cosine_sim(vec1: dict, vec2: dict) -> float:
    """Cosine similarity between two sparse vectors."""
    common = set(vec1.keys()) & set(vec2.keys())
    if not common:
        return 0.0
    num = sum(vec1[k] * vec2[k] for k in common)
    den1 = math.sqrt(sum(v*v for v in vec1.values()))
    den2 = math.sqrt(sum(v*v for v in vec2.values()))
    if den1 == 0 or den2 == 0:
        return 0.0
    return num / (den1 * den2)


def cluster_claims(claims: list[dict], embeddings: list[dict], threshold: float = 0.6) -> list[list[dict]]:
    """Cluster claims by embedding similarity."""
    n = len(claims)
    if n == 0:
        return []
    
    clusters = []
    assigned = [False] * n
    
    for i in range(n):
        if assigned[i]:
            continue
        cluster = [claims[i]]
        assigned[i] = True
        for j in range(i+1, n):
            if assigned[j]:
                continue
            sim = cosine_sim(embeddings[i], embeddings[j])
            if sim >= threshold:
                cluster.append(claims[j])
                assigned[j] = True
        clusters.append(cluster)
    
    return clusters

def group_by_theme(ledger: list[dict]) -> dict:
    themes = {
        "background": [],
        "current_evidence": [],
        "alternatives": [],
        "risks": [],
    }
    for c in ledger:
        text = c["claim"].lower()
        ctype = c["type"]
        
        # Background: foundational, theoretical, classic
        if any(w in text for w in ["theory", "framework", "construct", "classic", "foundational", "literature review", "meta-analysis"]):
            themes["background"].append(c)
        # Current evidence: empirical findings
        elif ctype == "fact" or any(w in text for w in ["study", "experiment", "empirical", "find", "show", "observed", "result"]):
            themes["current_evidence"].append(c)
        # Alternatives: competing, boundary, moderator
        elif any(w in text for w in ["alternative", "competing", "boundary", "moderator", "different", "versus", "compare"]):
            themes["alternatives"].append(c)
        # Risks: limitations, bias, validity
        elif any(w in text for w in ["limit", "bias", "validity", "risk", "caveat", "fail", "publication bias", "generalizab"]):
            themes["risks"].append(c)
        else:
            themes["current_evidence"].append(c)
    return themes

def phase4_synthesize(extraction: dict, rundir: Path, topic_profile: dict | None = None,
                      adjudicate: bool = False, topic: str = "") -> dict:
    print("  [Phase 4] Synthesizing: contradictions + themes...")
    
    ledger = extraction["claim_ledger"]
    matrix = extraction["evidence_matrix"]
    topic_terms = (topic_profile or {}).get("terms", []) + (topic_profile or {}).get("expanded", [])
    contradictions = detect_contradictions(ledger, topic_terms)

    # Optional: send the lexical candidates to an LLM for METHOD step 13 triage —
    # genuine empirical disagreement vs method/definition mismatch. This is the
    # judgment the keyword detector cannot make, and the step the method leans on.
    if adjudicate and contradictions:
        verdicts = ADJ.adjudicate_contradictions(contradictions, topic)
        if verdicts is not None:
            kinds = Counter(c["type"] for c in verdicts)
            print(f"  [Phase 4] Adjudicated {len(contradictions)} candidates -> "
                  f"{len(verdicts)} survive {dict(kinds)}")
            contradictions = verdicts

    themes = group_by_theme(ledger)

    synthesis = {
        "consensus": [],
        "disputed": [],
        "uncertain": [],
        "missing": [],
    }

    # Consensus = statements multiple independent sources make, quoted. The previous
    # version emitted claim-type histograms ("current_evidence: 132 claims
    # ({'derived': 121, ...})") as the report's Key Findings — a count of the
    # pipeline's own labels, not a finding about the topic.
    corroborated = [r for r in matrix if r["corroboration"] != "single-source"]
    corroborated.sort(key=lambda r: (r["strength"] == "high", r["corroboration"]), reverse=True)
    for row in corroborated[:8]:
        synthesis["consensus"].append(
            f"{row['claim'][:220]} — corroborated by {row['corroboration']} ({row['extracted_from']})")

    for c in contradictions[:6]:
        synthesis["disputed"].append(
            f"On '{c['topic']}': {len(c['sources_for'])} source(s) report a positive direction, "
            f"{len(c['sources_against'])} report the opposite. {c['status']}.")

    # Uncertain = hedged claims with no corroboration.
    hedged = [c for c in ledger if c["type"] in ("inference", "hypothesis")]
    for c in hedged[:6]:
        synthesis["uncertain"].append(f"{c['claim'][:200]} — {c['type']} ({c['source_title'][:60]})")

    for theme, claims in themes.items():
        if not claims:
            synthesis["missing"].append(f"No claims retrieved for subquestion '{theme}'")
        elif len(claims) < 3:
            synthesis["missing"].append(f"Thin coverage of '{theme}': {len(claims)} candidate(s) only")

    untyped = extraction["stats"]["untyped_share"]
    if untyped > 0.5:
        synthesis["missing"].append(
            f"{untyped:.0%} of candidates could not be typed by cue matching — "
            f"the ledger's type column is weak for this corpus.")
    if not corroborated:
        synthesis["missing"].append(
            "No claim was corroborated across independent sources — every statement below rests on one paper.")

    result = {
        "contradictions": contradictions,
        # Whether the LLM was ASKED to triage is not the same as whether it got
        # anything to triage. With zero lexical candidates the adjudicator is
        # never invoked, and the artifact must not then advise the reader to
        # "run with --adjudicate" — they just did.
        "adjudication_requested": bool(adjudicate),
        "themes": {k: [c["claim"][:250] for c in v] for k, v in themes.items()},
        "synthesis": synthesis,
        "synthesized_at": now_iso(),
    }
    (rundir / "SYNTHESIS.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [Phase 4] Contradictions: {len(contradictions)}; Themes populated")
    return result

# ============================================================
# PHASE 5 — AUDIT + PUBLISH
# ============================================================
def phase5_publish(plan: dict, ev: dict, extraction: dict, synthesis: dict, rundir: Path,
                   archetype: str, live_verify: bool = False) -> dict:
    print("  [Phase 5] Audit gates + artifact generation...")

    audit_res = R.verify_records(str(rundir / "EVIDENCE.json"), live=live_verify)
    (rundir / "AUDIT.json").write_text(json.dumps(audit_res, indent=2), encoding="utf-8")
    if not audit_res["pass"]:
        print(f"    G3 FAILED: {len(audit_res['unverified'])} unverified, "
              f"{len(audit_res.get('mismatched', []))} title mismatch(es)")
        for m in audit_res.get("mismatched", [])[:5]:
            print(f"      MISMATCH {m['key']}: stored '{m['stored_title'][:60]}' "
                  f"vs DOI '{m['doi_resolves_to'][:60]}'")
        return {"audit": audit_res, "published": False}

    print(f"    G3 PASSED: {audit_res['verified']} resolve, {audit_res['citable']} citable, "
          f"{len(audit_res['no_abstract'])} discovery-only"
          + (f", live DOI check clean" if live_verify else " (structural only)"))

    topic_slug = slugify(plan["topic"])
    md_path = CONFIG["output_dir"] / f"{topic_slug}.md"
    CONFIG["output_dir"].mkdir(parents=True, exist_ok=True)

    # Write to a .draft first: a file that fails G5 must not sit at the published path
    # looking finished. The old code printed "G5 FAILED" and returned published=True.
    draft_path = md_path.with_suffix(".draft.md")
    md = generate_markdown_artifact(plan, ev, extraction, synthesis, audit_res, archetype)
    draft_path.write_text(md, encoding="utf-8")

    g5 = R.check_artifact(str(draft_path))
    (rundir / "G5_CHECK.json").write_text(json.dumps(g5, indent=2), encoding="utf-8")
    if not g5["pass"]:
        print(f"    G5 FAILED — not published. Draft kept at {draft_path}")
        for k in ("missing_sections", "placeholders_found", "empty_sections"):
            if g5.get(k):
                print(f"      {k}: {g5[k]}")
        return {"audit": audit_res, "published": False, "g5": g5, "path": str(draft_path)}

    draft_path.replace(md_path)
    print(f"    G5 PASSED ({g5['n_sources']} sources, ledger types: {', '.join(g5['ledger_types_found'])})")
    print(f"    Artifact published: {md_path}")
    return {"audit": audit_res, "published": True, "g5": g5, "path": str(md_path)}

def generate_markdown_artifact(plan: dict, ev: dict, extraction: dict, synthesis: dict, audit: dict, archetype: str) -> str:
    topic = plan["topic"]
    ledger = extraction["claim_ledger"]
    matrix = extraction["evidence_matrix"]
    contradictions = synthesis["contradictions"]
    themes = synthesis["themes"]
    synth = synthesis["synthesis"]

    lines = []
    lines.append(f"# {topic}")
    lines.append("")
    lines.append(f"**Archetype:** {archetype}  ")
    lines.append(f"**Generated:** {now_iso()}  ")
    lines.append(f"**Records retrieved:** {len(ev['records'])}  ")
    lines.append(f"**Claims extracted:** {len(ledger)}  ")
    lines.append(f"**Audit:** {audit['verified']} verified, {len(audit['no_abstract'])} no_abstract  ")
    lines.append("")

    lines.append("## Brief")
    lines.append("")
    lines.append(f"**Objective:** {plan['objective']}")
    lines.append(f"**Success criteria:** Decision-ready evidence matrix with typed claims, contradiction analysis, and clear gaps.")
    lines.append("")

    lines.append("## Subquestions")
    lines.append("")
    for sq in plan["subquestions"]:
        lines.append(f"- **{sq['label']}**: {sq['prompt']}")
    lines.append("")

    lines.append("## Evidence Matrix")
    lines.append("")
    lines.append("Each row is a sentence auto-extracted from the cited source. *Corroboration* "
                 "is the only column carrying cross-source information; *Strength* scores the "
                 "**source**, not the claim.")
    lines.append("")
    lines.append("| Candidate claim | Type | Extracted from | Source | Corroboration | Source signal | Strength |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in matrix:
        claim = row["claim"].replace("|", "\\|")[:300]
        origin = row["extracted_from"].replace("|", "\\|")[:120]
        source = (row["source"] or "").replace("|", "\\|")[:100]
        signal = row["source_signal"].replace("|", "\\|")[:80]
        lines.append(f"| {claim} | {row['type']} | {origin} | {source} | "
                     f"{row['corroboration']} | {signal} | {row['strength']} |")
    lines.append("")

    lines.append("## Claim Ledger")
    lines.append("")
    lines.append("Each claim typed as **fact / derived / inference / hypothesis / recommendation**.")
    lines.append("")
    by_type = {}
    for c in ledger:
        by_type.setdefault(c["type"], []).append(c)
    for t in CLAIM_TYPES:
        if t in by_type:
            lines.append(f"### {t.capitalize()} ({len(by_type[t])})")
            for c in by_type[t][:15]:
                lines.append(f"- {c['claim'][:400]}  \n  *Source: {c['source_title']} ({c['source_year']}), {c['source_cites']} cites, relevance={c.get('source_relevance',0):.2f}, DOI: {c['source_doi']}*")
            lines.append("")

    adjudicated = any(c.get("verification") == "llm-adjudicated" for c in contradictions)
    adj_requested = synthesis.get("adjudication_requested", False)
    lines.append("## Contradiction Analysis")
    lines.append("")
    if adjudicated:
        lines.append(f"Method: lexical direction-of-effect comparison across {len(ledger)} "
                     "candidate sentences, then **LLM triage of each candidate** "
                     "(METHOD step 13) separating genuine empirical disagreement from "
                     "method, definition, and scope mismatch. Candidates judged not to "
                     "be conflicts have been dropped. Each verdict below carries its "
                     "own confidence — a low-confidence verdict means the extracted "
                     "sentences did not carry enough context to decide.")
    else:
        lines.append(f"Method: lexical direction-of-effect comparison across {len(ledger)} candidate "
                     "sentences, grouped by topic term and clustered by TF-IDF similarity. "
                     "Detects *opposing wording*, not adjudicated empirical conflict — "
                     "every item below is a triage prompt (METHOD step 13). "
                     + ("**LLM adjudication was enabled but never invoked here: the lexical "
                        "detector is a pre-filter, and it surfaced nothing for the model to "
                        "triage.** The bottleneck on this section is detection, not triage — "
                        "`--adjudicate` cannot improve it."
                        if adj_requested else
                        "Run with `--adjudicate` to have these triaged."))
    lines.append("")
    if contradictions:
        for c in contradictions:
            lines.append(f"### {c['topic']} — {c['type']} ({c['status']})")
            lines.append("**Reported positive direction:**")
            for claim in c["claims_for"]:
                lines.append(f"- {claim}")
            lines.append("**Reported negative / null direction:**")
            for claim in c["claims_against"]:
                lines.append(f"- {claim}")
            lines.append(f"**Sources (positive):** {', '.join(c['sources_for'])}")
            lines.append(f"**Sources (negative):** {', '.join(c['sources_against'])}")
            if c.get("verification") == "llm-adjudicated":
                lines.append(f"**Assessment:** {c['resolution']}")
                lines.append(f"**What would settle it:** {c['what_would_settle_it']}")
            else:
                lines.append(f"**Before calling this a conflict:** {c['resolution']}")
            lines.append("")
    else:
        lines.append("The detector found no opposing-direction pairs in this corpus. "
                     "That is a **negative result from a lexical heuristic, not evidence "
                     "of agreement in the literature** — a corpus retrieved from a single "
                     "topical query is unlikely to surface its own dissent. Treat the "
                     "contradiction pass as UNCOMPLETED and run a manual adversarial "
                     "search before relying on this section.")
        lines.append("")

    lines.append("## Gaps")
    lines.append("")
    for gap in synth["missing"]:
        lines.append(f"- {gap}")
    for theme, claims in themes.items():
        # claims are strings (claim texts), not dicts
        if len(claims) < 3:
            lines.append(f"- Thin evidence for **{theme}** ({len(claims)} claims)")
    lines.append("")

    lines.append("## Synthesis")
    lines.append("")
    for cat in ["consensus", "disputed", "uncertain", "missing"]:
        if synth[cat]:
            lines.append(f"### {cat.capitalize()}")
            for item in synth[cat]:
                lines.append(f"- {item}")
            lines.append("")

    lines.append("## Conclusions & Recommendation")
    lines.append("")

    stats = extraction["stats"]
    ft = stats["by_provenance"].get("fulltext", 0)
    n_multi = stats["multi_source_claims"]
    # Confidence is derived from what the run actually achieved, not asserted.
    if n_multi >= 5 and ft >= 5 and contradictions:
        confidence = "Moderate"
    elif n_multi >= 1:
        confidence = "Low"
    else:
        confidence = "Very low"
    lines.append(f"**Confidence: {confidence}.** Basis: {n_multi} of {len(ledger)} candidates "
                 f"corroborated across independent sources; {ft} drawn from fulltext and "
                 f"{stats['by_provenance'].get('abstract', 0)} from abstracts alone; "
                 f"{stats['untyped_share']:.0%} could not be typed; "
                 f"{len(contradictions)} contradiction candidate(s) found.")
    lines.append("")
    llm_typed = sum(1 for c in ledger if str(c.get("type_confidence", "")).startswith("llm-"))
    if llm_typed:
        lines.append(f"> This artifact is machine-extracted. Sentences were selected by "
                     f"heuristic, then {llm_typed} of {len(ledger)} were typed by an LLM "
                     f"reading each sentence — a real improvement over cue matching, but "
                     f"still a model judging a fragment without the surrounding paper, and "
                     f"no human has checked it. Treat low-confidence labels as unresolved. "
                     f"It is a **research starting point, not a finding set** — cite nothing "
                     f"from it without opening the source.")
    else:
        lines.append("> This artifact is machine-extracted. Sentences below were selected by "
                     "heuristic from abstracts and fulltext; none has been read in context and "
                     "adjudicated by an analyst. It is a **research starting point, not a "
                     "finding set** — cite nothing from it without opening the source.")
    lines.append("")

    lines.append("**Cross-source statements** (the only claims here with more than one source):")
    if synth["consensus"]:
        for item in synth["consensus"][:5]:
            lines.append(f"- {item}")
    else:
        lines.append("- None. Every candidate in this report rests on a single paper.")
    lines.append("")

    # Next actions are derived from the run's own weak spots, not a fixed string.
    next_steps = []
    if ft < CONFIG["top_k_fulltext"]:
        next_steps.append(f"Fulltext was obtained for only {ft} papers — retry the "
                          f"{CONFIG['top_k_fulltext'] - ft} that failed, or fetch them manually.")
    if not contradictions:
        next_steps.append("Run an adversarial search (critiques, failed replications, "
                          "null results) — the automated contradiction pass found nothing.")
    if stats["untyped_share"] > 0.4:
        next_steps.append(f"{stats['untyped_share']:.0%} of candidates are untyped; "
                          "type the ones you intend to cite by hand.")
    if n_multi == 0:
        next_steps.append("Seek independent corroboration before relying on any statement here.")
    thin = [t for t, cl in themes.items() if len(cl) < 3]
    if thin:
        next_steps.append(f"Thin subquestion coverage: {', '.join(thin)} — widen those queries.")
    next_steps.append(f"Verify citations against registrars: "
                      f"`python tools/retrieval.py verify <EVIDENCE.json> --live`")

    lines.append("**Next actions:**")
    for s in next_steps:
        lines.append(f"- {s}")
    lines.append("")

    lines.append("## Sources")
    lines.append("")
    for rec_id, rec in ev["records"].items():
        title = rec.get("title", "Untitled")
        year = rec.get("year", "?")
        venue = rec.get("venue", "")
        doi = rec.get("doi", "")
        relevance = rec.get("relevance_score", 0)
        lines.append(f"- {title} ({year}). *{venue}*. Relevance: {relevance:.2f}. DOI: {doi}")
    lines.append("")

    lines.append("## Audit")
    lines.append("")
    lines.append(f"- **G1 Retrieve:** {len(ev['records'])} records in the evidence set, "
                 f"{len(ev.get('discovery_only', {}))} screened to discovery-only (EVIDENCE.json)")
    lines.append(f"- **G2 Extract:** {len(ledger)} candidates, {extraction['stats']['typed']} typed "
                 f"({extraction['stats']['untyped_share']:.0%} untyped) (CLAIM_LEDGER.json)")
    lines.append(f"- **G3 Sources:** {audit['verified']} resolve, {len(audit['no_abstract'])} without "
                 f"usable abstract (not citable), {len(audit['unverified'])} unverified — "
                 f"**{'PASS' if audit['pass'] else 'FAIL'}**")
    if not audit.get("live_check"):
        lines.append("  - *Structural only. DOIs were not re-resolved against Crossref; "
                     "run `verify --live` to check that each stored title matches what its DOI "
                     "actually points to.*")
    else:
        lines.append(f"  - Live DOI cross-check: {len(audit.get('mismatched', []))} title mismatch(es), "
                     f"{len(audit.get('unchecked', []))} unchecked.")
    lines.append(f"- **G4 High-stakes:** {'FLAGGED — human review required before authority use' if archetype == 'high_stakes_factual' else 'Not flagged (archetype: ' + archetype + ')'}")
    lines.append(f"- **G5 Structure:** evaluated after this file is written; result in G5_CHECK.json")
    lines.append("")
    lines.append("**What these gates do not check:** that a claim is true, that the extracted "
                 "sentence means what it appears to mean out of context, that the corpus is "
                 "representative of the literature, or that important work is missing. "
                 "Those remain the analyst's job.")
    lines.append("")

    return "\n".join(lines)

# ============================================================
# MAIN
# ============================================================
def main():
    ap = argparse.ArgumentParser(
        prog="run_research.py",
        description="peak-research orchestrator — Plan → Retrieve → Extract → "
                    "Synthesize → Audit, gated G1–G5.")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--queries", default="", help="comma-separated OpenAlex search stems")
    ap.add_argument("--year", default="2022-01-01")
    ap.add_argument("--archetype", default="literature_review",
                    choices=["literature_review", "architecture_decision", "high_stakes_factual"])
    ap.add_argument("--output-dir", default=None,
                    help="where the .md deliverable goes "
                         "(default: $PEAK_OUTPUT_DIR, else ./research)")
    ap.add_argument("--workspace", default=None,
                    help="where run state and the API cache live "
                         "(default: $PEAK_WORKSPACE, else ./.peak-research)")
    ap.add_argument("--live-verify", action="store_true",
                    help="G3: re-resolve every DOI via Crossref and fail on title mismatch")
    ap.add_argument("--adjudicate", action="store_true",
                    help="use an LLM to type claims and triage contradictions instead "
                         "of lexical heuristics (needs `pip install anthropic` + "
                         "credentials; costs money; falls back silently if absent)")
    args = ap.parse_args()

    # Workspace first: retrieval.py resolved CACHE_DIR at import time, so a late
    # --workspace has to re-point it (and the cost log that hangs off it) or the flag
    # would move the run state and silently leave the cache behind.
    if args.workspace:
        os.environ["PEAK_WORKSPACE"] = args.workspace
        os.environ.pop("PEAK_CACHE_DIR", None)
        R.CACHE_DIR = str(P.cache_dir())
        R.COST_LOG = os.path.join(R.CACHE_DIR, "cost_log.json")

    if args.adjudicate:
        ok, reason = ADJ.available()
        if ok:
            print(f"  Adjudication ENABLED via {ADJ.MODEL} (cached responses cost $0)")
        else:
            print(f"  NOTE: --adjudicate requested but unavailable ({reason}).")
            print("        Continuing with lexical heuristics.")
            args.adjudicate = False

    CONFIG["year_from"] = args.year
    if args.output_dir:
        CONFIG["output_dir"] = Path(args.output_dir)
    if args.archetype == "high_stakes_factual" and not args.live_verify:
        print("  NOTE: high_stakes_factual archetype — enabling --live-verify (G4).")
        args.live_verify = True
    queries = [q.strip() for q in args.queries.split(",") if q.strip()] or None

    # Run state goes to the user's workspace, not next to this script: the plugin
    # install is shared and gets replaced on update.
    rundir = P.runs_dir() / slugify(args.topic)
    rundir.mkdir(parents=True, exist_ok=True)
    print(f"== peak-research run: {args.topic}")
    print(f"   rundir:    {rundir}")
    print(f"   cache:     {R.CACHE_DIR}")
    print(f"   output:    {CONFIG['output_dir']}")
    print(f"   archetype: {args.archetype}")

    print("\n[PHASE 1] PLAN")
    plan = phase1_plan(args.topic, queries, args.archetype, rundir)

    print("\n[PHASE 2] RETRIEVE (4-pass + expansion, subagent-parallel)")
    try:
        ev = phase2_retrieve(plan, rundir)
    except RetrievalGateError as e:
        print(f"\n{e}")
        print("== ABORTED at G1: no artifact written ==")
        sys.exit(2)

    print("\n[PHASE 3] EXTRACT (claim typing + fulltext)")
    extraction = phase3_extract(ev, rundir, adjudicate=args.adjudicate, topic=args.topic)

    print("\n[PHASE 4] SYNTHESIZE (contradictions + themes)")
    synthesis = phase4_synthesize(extraction, rundir, ev.get("topic_profile"),
                                  adjudicate=args.adjudicate, topic=args.topic)

    print("\n[PHASE 5] AUDIT + PUBLISH")
    result = phase5_publish(plan, ev, extraction, synthesis, rundir, args.archetype,
                            live_verify=args.live_verify)

    if result.get("published"):
        print(f"\n== COMPLETE: {result['path']} ==")
    else:
        print(f"\n== INCOMPLETE: audit failed ==")
        sys.exit(2)

if __name__ == "__main__":
    main()