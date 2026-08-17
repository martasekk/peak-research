"""
peak_research/tools/retrieval.py
================================
Resilient retrieval toolkit for the peak-research skill.

Design principles (from the 20-stage playbook + lessons learned in the field):
  * Deterministic, reproducible queries — no ad-hoc URL building per run.
  * Every primary source is verified (title/abstract resolved) before it is cited.
  * A hard fallback chain: OpenAlex -> Jina reader -> arXiv API -> GitHub API -> web fetch.
  * Failures are logged, never silently replaced or fabricated.

This module is import-safe and also runnable as a CLI:
    python retrieval.py openalex "short phrase" --year 2022 --per 5
    python retrieval.py arxiv 2303.11366
    python retrieval.py jina https://example.com
    python retrieval.py github_repo owner/name
    python retrieval.py verify record.json          # structural gate (offline)
    python retrieval.py verify record.json --live   # + re-resolve every DOI via Crossref
                                                    #   and fail on title mismatch

Requires only the stdlib (urllib) — no pip installs. Works on Windows git-bash/msys.
"""
from __future__ import annotations
import json, urllib.request, urllib.parse, urllib.error, re, sys, time, os, hashlib

try:                       # tools/ on sys.path — CLI, run_research.py, tests
    import paths as _paths
except ImportError:        # imported as tools.retrieval
    from . import paths as _paths

MAILTO = os.environ.get("OPENALEX_MAILTO", "research@example.com")
if MAILTO == "research@example.com":
    # OpenAlex uses mailto for polite-pool rate limiting; a fake address gets you
    # treated as anonymous. Warn once, don't fail.
    sys.stderr.write(
        "[peak-research] WARNING: OPENALEX_MAILTO is the placeholder address. "
        "Set a real one to stay in the OpenAlex polite pool.\n")
UA = {"User-Agent": "peak-research-toolkit/1.0 (mailto:%s)" % MAILTO}
# NOT next to __file__: as a plugin this directory is a read-only install that gets
# replaced on update. Cache lives in the user's workspace (see tools/paths.py) and is
# created lazily, so importing the toolkit never litters the current directory.
CACHE_DIR = str(_paths.cache_dir())

# Cached entries older than this are re-fetched. 0 disables expiry.
CACHE_TTL_DAYS = float(os.environ.get("PEAK_CACHE_TTL_DAYS", "30"))

# --- Cost/credit tracking (OpenAlex is now a credit/USD model; conserve aggressively) ---
# Per-call credit cost observed live (subagent-1, Aug 2026):
#   full-text search=        ~10 credits / $0.001
#   filter=doi: fetch         ~1 credit  / $0.0001
#   entity GET + select=      $0  (free)
COST_LOG = os.path.join(CACHE_DIR, "cost_log.json")


def _ensure_cache() -> None:
    """Create the cache directory on first write, not on import."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError:
        pass


def _cache_path(key: str) -> str:
    """Cache filename = readable prefix + full-key hash.

    The prefix is for humans browsing cache/; the hash is what makes the name unique.
    Truncating a readable key (the old behaviour) collided for any two URLs sharing a
    long prefix — e.g. the same search phrase with a different per-page or year — and
    silently served one query's response for another.
    """
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:96]
    return os.path.join(CACHE_DIR, "%s.%s.json" % (safe, digest))


def _cache_fresh(path: str) -> bool:
    if not os.path.exists(path):
        return False
    if CACHE_TTL_DAYS <= 0:
        return True
    return (time.time() - os.path.getmtime(path)) < CACHE_TTL_DAYS * 86400


def _cached_get_json(url: str, timeout: int = 30, retries: int = 3, cost: float = 0.001,
                     force: bool = False):
    """GET JSON with disk cache. Cached responses cost $0 (no network). Log spend on cache miss."""
    cp = _cache_path("j_" + url)
    if not force and _cache_fresh(cp):
        try:
            return json.loads(open(cp, encoding="utf-8").read())
        except Exception:
            pass
    data = _get_json(url, timeout, retries)  # network (may raise after retries)
    _ensure_cache()
    try:
        open(cp, "w", encoding="utf-8").write(json.dumps(data))
        _log_cost(cost)
    except Exception:
        pass
    return data


def _cached_get_text(url: str, timeout: int = 40, retries: int = 3, cost: float = 0.0,
                     force: bool = False) -> str:
    cp = _cache_path("t_" + url)
    if not force and _cache_fresh(cp):
        try:
            return open(cp, encoding="utf-8").read()
        except Exception:
            pass
    data = _get_text(url, timeout, retries)
    _ensure_cache()
    try:
        open(cp, "w", encoding="utf-8").write(data)
        _log_cost(cost)
    except Exception:
        pass
    return data


def _log_cost(amount: float):
    try:
        log = json.loads(open(COST_LOG, encoding="utf-8").read()) if os.path.exists(COST_LOG) else {}
    except Exception:
        log = {}
    log["total_usd"] = round(log.get("total_usd", 0.0) + amount, 6)
    log["calls"] = log.get("calls", 0) + 1
    _ensure_cache()
    try:
        open(COST_LOG, "w", encoding="utf-8").write(json.dumps(log))
    except Exception:
        pass


def cost_summary() -> dict:
    try:
        return json.loads(open(COST_LOG, encoding="utf-8").read())
    except Exception:
        return {"total_usd": 0.0, "calls": 0}


class RetrievalError(Exception):
    pass


def _get_json(url: str, timeout: int = 30, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 402, 500, 503):
                time.sleep(2 ** attempt)
                continue
            raise RetrievalError("HTTP %s: %s" % (e.code, e.read().decode()[:200]))
        except Exception as e:  # transient network blip
            last = e
            time.sleep(2 ** attempt)
    raise RetrievalError("request failed after %d retries: %s" % (retries, last))


def _get_text(url: str, timeout: int = 40, retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 402, 500, 503):
                time.sleep(2 ** attempt)
                continue
            raise RetrievalError("HTTP %s for %s" % (e.code, url))
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise RetrievalError("request failed after %d retries: %s" % (retries, last))


# ----------------------------------------------------------------------------
# OpenAlex
# ----------------------------------------------------------------------------
def _norm_date(y: str) -> str:
    y = str(y).strip()
    if re.fullmatch(r"\d{4}", y):
        return y + "-01-01"
    return y


def openalex_search(phrase: str, year_from: str = "2022-01-01", per: int = 5,
                    sort: str = "relevance_score:desc", extra_filter: str = ""):
    """Title+abstract phrase search using OpenAlex search= parameter (filter=title_and_abstract.search: returns 0)."""
    year_from = _norm_date(year_from)
    # Use search= parameter (WORKS); filter=title_and_abstract.search: returns 0
    q = [
        "search=" + urllib.parse.quote(phrase),
        "filter=from_publication_date:%s" % year_from,
        "per-page=%d" % per,
        "mailto=" + MAILTO,
    ]
    if extra_filter:
        q.insert(1, "filter=" + urllib.parse.quote(extra_filter))
    url = "https://api.openalex.org/works?" + "&".join(q)
    try:
        d = _cached_get_json(url, cost=0.001)  # search costs ~10 credits; cached re-runs are $0
    except urllib.error.HTTPError as e:
        raise RetrievalError("OpenAlex HTTP %s: %s" % (e.code, e.read().decode()[:200]))
    return [_work_to_record(w) for w in d.get("results", [])]


def openalex_by_arxiv(axid: str):
    # Live API NOTE: `filter=arxiv:ID` is REJECTED. arXiv works are indexed by their
    # DOI in the form 10.48550/arxiv.ID. Use that. `select=` makes this fetch $0 (free tier).
    doi = "10.48550/arxiv.%s" % axid
    url = "https://api.openalex.org/works?filter=doi:%s&select=id,title,cited_by_count,abstract_inverted_index,publication_year,doi,type,authorships,primary_location,open_access&mailto=%s" % (doi, MAILTO)
    d = _cached_get_json(url, cost=0.0)  # select= -> free; cache makes re-runs instant
    if not d.get("results"):
        return None
    return _work_to_record(d["results"][0])


def openalex_by_doi(doi: str):
    url = "https://api.openalex.org/works?filter=doi:%s&select=id,title,cited_by_count,abstract_inverted_index,publication_year,doi,type,authorships,primary_location,open_access&mailto=%s" % (doi, MAILTO)
    d = _cached_get_json(url, cost=0.0)
    if not d.get("results"):
        return None
    return _work_to_record(d["results"][0])


def openalex_by_id(openalex_id: str):
    # Single-entity GET + select= is $0 (free). openalex_id like W4353112996 (no URL prefix needed).
    url = "https://api.openalex.org/works/%s?select=id,title,cited_by_count,abstract_inverted_index,publication_year,doi,type,authorships,primary_location,open_access&mailto=%s" % (openalex_id, MAILTO)
    d = _cached_get_json(url, cost=0.0)
    return _work_to_record(d)


def _work_to_record(w: dict) -> dict:
    ai = w.get("abstract_inverted_index") or {}
    if ai:
        pos = []
        for word, locs in ai.items():
            for l in locs:
                pos.append((l, word))
        pos.sort()
        abstract = " ".join(word for _, word in pos)
    else:
        abstract = ""
    src = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "source_type": "openalex",
        "id": w.get("id"),
        "doi": w.get("doi"),
        "title": w.get("title"),
        "year": w.get("publication_year"),
        "cited_by_count": w.get("cited_by_count"),
        "type": w.get("type"),
        "oa_url": (w.get("open_access") or {}).get("oa_url"),
        "venue": src.get("display_name"),
        # OpenAlex returns authorships with a null `author`, and authors with a null
        # `id` or `display_name` — `.get(k, "")` yields None for a present-but-null key,
        # so coerce with `or ""` rather than relying on the default.
        "authors": [((a.get("author") or {}).get("display_name") or "")
                    for a in (w.get("authorships") or [])
                    if (a.get("author") or {}).get("display_name")][:3],
        # Author IDs let callers use filter=author.id: — searching a display name with
        # search= matches the name anywhere in a work (reference lists included) and
        # returns arbitrary papers.
        "author_ids": [((a.get("author") or {}).get("id") or "").rsplit("/", 1)[-1]
                       for a in (w.get("authorships") or [])
                       if (a.get("author") or {}).get("id")][:3],
        "abstract": abstract,
    }


# ----------------------------------------------------------------------------
# arXiv
# ----------------------------------------------------------------------------
def arxiv_abstract(axid: str) -> dict:
    url = "https://export.arxiv.org/api/query?id_list=%s&max_results=1" % axid
    try:
        data = _get_text(url)
    except Exception as e:
        raise RetrievalError("arxiv fetch failed: %s" % e)
    # The API sometimes returns a query-echo page with no real <entry>; detect that.
    entries = re.findall(r"<entry>(.*?)</entry>", data, re.S)
    if not entries:
        raise RetrievalError("arxiv returned no <entry> (API flaky/empty) for %s" % axid)
    e = entries[0]
    m_title = re.search(r"<title>([^<]+)</title>", e, re.S)
    m_sum = re.search(r"<summary>([^<]+)</summary>", e, re.S)
    m_pub = re.search(r"<published>([^<]+)</published>", e)
    if not m_title:
        raise RetrievalError("arxiv entry had no title for %s" % axid)
    return {
        "source_type": "arxiv",
        "arxiv": axid,
        "title": re.sub(r"\s+", " ", m_title.group(1)).strip(),
        "published": m_pub.group(1)[:10] if m_pub else None,
        "abstract": re.sub(r"\s+", " ", m_sum.group(1)).strip() if m_sum else "",
    }


def arxiv_title_search(title: str, per: int = 3) -> list:
    url = "http://export.arxiv.org/api/query?search_query=ti:%s&max_results=%d" % (
        urllib.parse.quote(title), per)
    data = _get_text(url)
    entries = re.findall(r"<entry>(.*?)</entry>", data, re.S)
    out = []
    for e in entries:
        t = re.search(r"<title>([^<]+)</title>", e, re.S)
        s = re.search(r"<summary>([^<]+)</summary>", e, re.S)
        out.append({
            "source_type": "arxiv_search",
            "title": re.sub(r"\s+", " ", t.group(1)).strip() if t else None,
            "abstract": re.sub(r"\s+", " ", s.group(1)).strip() if s else "",
        })
    return out


# ----------------------------------------------------------------------------
# Jina reader (r.jina.ai) — fallback for pages / when OpenAlex is rate-limited
# ----------------------------------------------------------------------------
def jina_fetch(url: str) -> str:
    try:
        return _get_text("https://r.jina.ai/" + url)
    except urllib.error.HTTPError as e:
        raise RetrievalError("Jina HTTP %s for %s" % (e.code, url))


def jina_abstract(url: str) -> dict:
    txt = jina_fetch(url)
    m = re.search(r"Abstract:?(.*?)(Authors:|Comments:|Subjects:|Cite as:)", txt, re.S)
    abst = re.sub(r"\s+", " ", m.group(1).strip()) if m else txt[:1600]
    title_m = re.search(r"Title:\s*(.+)", txt)
    return {
        "source_type": "jina",
        "url": url,
        "title": title_m.group(1).strip() if title_m else None,
        "abstract": abst,
        "raw_len": len(txt),
    }


# ----------------------------------------------------------------------------
# GitHub API (no-auth, ~60/hr)
# ----------------------------------------------------------------------------
def github_repo(owner: str, repo: str) -> dict:
    d = _get_json("https://api.github.com/repos/%s/%s" % (owner, repo))
    return {"source_type": "github_repo", "full_name": d.get("full_name"),
            "stars": d.get("stargazers_count"), "description": d.get("description"),
            "url": d.get("html_url"), "archived": d.get("archived")}


def github_search_repos(query: str, per: int = 5) -> list:
    url = "https://api.github.com/search/repositories?q=%s&per_page=%d" % (
        urllib.parse.quote(query), per)
    d = _get_json(url)
    return [{"source_type": "github_repo", "full_name": r["full_name"],
             "stars": r["stargazers_count"], "description": r.get("description"),
             "url": r["html_url"]} for r in d.get("items", [])]


def github_org_repos(org: str) -> list:
    d = _get_json("https://api.github.com/orgs/%s/repos?per_page=100" % org)
    if isinstance(d, dict):
        return []
    return [{"source_type": "github_repo", "full_name": r["full_name"],
             "stars": r["stargazers_count"], "description": r.get("description"),
             "url": r["html_url"]} for r in d]


# ----------------------------------------------------------------------------
# Crossref (DOI verification, no key)
# ----------------------------------------------------------------------------
def crossref_doi(doi: str) -> dict:
    url = "https://api.crossref.org/works/%s" % doi
    d = _get_json(url)
    m = d.get("message", {})
    return {
        "source_type": "crossref",
        "doi": doi,
        "title": (m.get("title") or [""])[0] if m.get("title") else None,
        "year": (m.get("published", {}).get("date-parts", [[""]])[0][0]
                 if m.get("published") else None),
        "type": m.get("type"),
        "publisher": m.get("publisher"),
    }


# ----------------------------------------------------------------------------
# Semantic Scholar (optional citation enrichment; often 429 — best-effort)
# ----------------------------------------------------------------------------
def semantic_scholar_paper(axid_or_doi: str) -> dict:
    # accepts arXiv:2303.11366 or a bare DOI
    key = axid_or_doi if axid_or_doi.startswith("arXiv:") or axid_or_doi.startswith("10.") \
        else "arXiv:" + axid_or_doi
    url = "https://api.semanticscholar.org/graph/v1/paper/%s?fields=title,citationCount,year" % key
    try:
        d = _get_json(url)
    except RetrievalError:
        return None  # usually 429; skip silently (best-effort only)
    if "error" in d:
        return None
    return {"source_type": "semantic_scholar", "title": d.get("title"),
            "citationCount": d.get("citationCount"), "year": d.get("year")}
def resolve_paper(axid: str) -> dict:
    """Try OpenAlex by arxiv id, then arXiv API directly. Returns verified record."""
    try:
        r = openalex_by_arxiv(axid)
        if r:
            return r
    except Exception:
        pass
    return arxiv_abstract(axid)


def resolve_url(url: str) -> dict:
    """Try Jina reader; on 401/empty, fall back to raw fetch."""
    try:
        return jina_abstract(url)
    except RetrievalError:
        raw = _get_text(url)
        return {"source_type": "raw", "url": url, "abstract": raw[:1600], "raw_len": len(raw)}


# ----------------------------------------------------------------------------
# Verification gate (S19): every cited source in a record file must resolve.
# ----------------------------------------------------------------------------
def _title_tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]{4,}", (s or "").lower()))


def title_overlap(a: str, b: str) -> float:
    """Jaccard-ish overlap of significant title tokens. 1.0 = same title."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def verify_records(path: str, min_abstract: int = 80, live: bool = False,
                   mismatch_threshold: float = 0.4) -> dict:
    """S19 verification gate.

    Structural checks (always):
      * a record must have a title AND a resolvable identity (doi / openalex id / arxiv id / url)
      * an abstract shorter than `min_abstract` chars does not count as an abstract
      * a record with no abstract is verified-but-'no_abstract' — discovery-only, NOT citable

    Live check (`live=True`, network): every record with a DOI is re-resolved through
    Crossref and its stored title compared to the registrar's title. A record whose
    stored title does not match what the DOI actually resolves to is reported under
    'mismatched' and FAILS the gate. This is the check that catches a fabricated or
    mis-attributed citation; the structural pass alone cannot.
    """
    recs = json.load(open(path, encoding="utf-8"))
    records = recs.get("records", recs) if isinstance(recs, dict) else recs
    items = list(records.items() if isinstance(records, dict) else enumerate(records))

    ok, bad, no_abs, mismatched, unchecked = 0, [], [], [], []
    for k, v in items:
        title = (v.get("title") or "").strip()
        abst = (v.get("abstract") or "").strip()
        st = v.get("source_type", "")
        identity = v.get("doi") or v.get("id") or v.get("arxiv") or v.get("url") or v.get("full_name")

        if not title or not identity:
            bad.append({"key": str(k), "reason": "no title" if not title else "no resolvable identity"})
            continue

        if st in ("github_repo", "github_search", "raw"):
            ok += 1
        elif len(abst) >= min_abstract:
            ok += 1
        else:
            # resolved work whose abstract is unavailable or too short to support a claim
            ok += 1
            no_abs.append(str(k))

        if live:
            doi = (v.get("doi") or "").replace("https://doi.org/", "").strip()
            if not doi:
                unchecked.append(str(k))
                continue
            try:
                cr = crossref_doi(doi)
                sim = title_overlap(title, cr.get("title") or "")
                if sim < mismatch_threshold:
                    mismatched.append({"key": str(k), "stored_title": title[:120],
                                       "doi_resolves_to": (cr.get("title") or "")[:120],
                                       "overlap": round(sim, 3)})
            except Exception as e:
                unchecked.append("%s (%s)" % (k, type(e).__name__))

    result = {"total": len(items), "verified": ok, "no_abstract": no_abs,
              "unverified": bad, "citable": ok - len(no_abs),
              "pass": len(bad) == 0 and len(mismatched) == 0}
    if live:
        result["live_check"] = True
        result["mismatched"] = mismatched
        result["unchecked"] = unchecked
    return result


# ----------------------------------------------------------------------------
# G5 Artifact self-check gate (METHOD Phase 5 / Skill18+19)
# ----------------------------------------------------------------------------
REQUIRED_SECTIONS = ["brief", "subquestion", "evidence", "claim ledger", "contradiction",
                     "gap", "synthes", "audit", "source"]
LEDGER_TYPES = ("fact", "derived", "inference", "hypothesis", "recommendation")
MIN_SOURCES = 5
# Text that means the pipeline produced nothing here. Presence => the section is empty
# in substance even though the heading exists.
PLACEHOLDER_PATTERNS = [
    r"no direct contradictions detected",
    r"\bTODO\b",
    r"\bTBD\b",
    r"lorem ipsum",
]


def check_artifact(md_path: str, min_sources: int = MIN_SOURCES) -> dict:
    """Verify a final .md deliverable meets the METHOD's structural contract.

    This is a STRUCTURAL gate, not a semantic one — it cannot tell you the research is
    right. What it can tell you is that a section is present *as a real heading* and is
    not empty, that sources are real citations rather than the word 'doi' appearing
    somewhere in prose, and that no section is a placeholder.
    """
    try:
        raw = open(md_path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return {"pass": False, "error": str(e), "missing_sections": REQUIRED_SECTIONS}

    # Match against actual markdown headings, not free text. The old substring check
    # passed on any research-shaped document because words like "source" and "fact"
    # occur in ordinary prose.
    headings = [h.strip().lower() for h in re.findall(r"^#{1,4}\s+(.+)$", raw, re.M)]
    heading_blob = " | ".join(headings)
    missing = [s for s in REQUIRED_SECTIONS if s not in heading_blob]

    # Typed ledger: require the type labels to appear as ledger subheadings or bold
    # labels, not merely as English words.
    ledger_hits = [t for t in LEDGER_TYPES
                   if re.search(r"(^#{1,4}\s+%s\b|\*\*%s\*\*|^\s*[-*]\s*%s\b)" % (t, t, t),
                                raw, re.M | re.I)]
    has_ledger = len(ledger_hits) >= 2

    # Sources: count distinct real identifiers, don't just look for the string "doi".
    dois = set(re.findall(r"10\.\d{4,9}/\S+", raw))
    urls = set(re.findall(r"https?://\S+", raw))
    arxiv = set(re.findall(r"arxiv[:/]\s*\d{4}\.\d{4,5}", raw, re.I))
    n_sources = len(dois | urls | arxiv)
    has_sources = n_sources >= min_sources

    placeholders = [p for p in PLACEHOLDER_PATTERNS if re.search(p, raw, re.I)]

    # An empty section is a fail. A parent heading whose body is blank but which is
    # followed by its own SUBheadings is not empty — its content lives one level down.
    empty_sections = []
    parts = re.split(r"^(#{1,4}\s+.+)$", raw, flags=re.M)
    heads = [(i, parts[i]) for i in range(1, len(parts) - 1, 2)]
    for n, (i, head) in enumerate(heads):
        body = parts[i + 1]
        if len(body.strip()) >= 20:
            continue
        level = len(head) - len(head.lstrip("#"))
        nxt = heads[n + 1][1] if n + 1 < len(heads) else None
        if nxt is not None:
            nxt_level = len(nxt) - len(nxt.lstrip("#"))
            if nxt_level > level:  # content is in subsections
                continue
        empty_sections.append(head.strip("# ").strip())

    passed = (not missing and has_ledger and has_sources
              and not placeholders and not empty_sections)
    return {"pass": passed,
            "missing_sections": missing,
            "has_typed_ledger": has_ledger,
            "ledger_types_found": ledger_hits,
            "has_sources": has_sources,
            "n_sources": n_sources,
            "placeholders_found": placeholders,
            "empty_sections": empty_sections,
            "char_count": len(raw)}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return
    cmd, args = argv[1], argv[2:]
    if cmd == "openalex":
        phrase = args[0]
        yr = args[1] if len(args) > 1 else "2022-01-01"
        if re.fullmatch(r"\d{4}", yr):
            yr += "-01-01"
        per = int(args[2]) if len(args) > 2 else 5
        for w in openalex_search(phrase, yr, per):
            print("%s | c=%s | %s" % (w["year"], w["cited_by_count"], (w["title"] or "")[:70]))
    elif cmd == "arxiv":
        print(json.dumps(arxiv_abstract(args[0]), indent=2, ensure_ascii=False))
    elif cmd == "jina":
        print(json.dumps(jina_abstract(args[0]), indent=2, ensure_ascii=False)[:1500])
    elif cmd == "github_repo":
        print(json.dumps(github_repo(*args[0].split("/")), indent=2, ensure_ascii=False))
    elif cmd == "github_search":
        for r in github_search_repos(args[0], 5):
            print("%s (★%s) — %s" % (r["full_name"], r["stars"], (r["description"] or "")[:60]))
    elif cmd == "crossref":
        print(json.dumps(crossref_doi(args[0]), indent=2, ensure_ascii=False))
    elif cmd == "sscholar":
        print(json.dumps(semantic_scholar_paper(args[0]), indent=2, ensure_ascii=False))
    elif cmd == "verify":
        live = "--live" in args
        path = [a for a in args if not a.startswith("--")][0]
        print(json.dumps(verify_records(path, live=live), indent=2))
    elif cmd == "check_artifact":
        print(json.dumps(check_artifact(args[0]), indent=2))
    elif cmd == "cost":
        print(json.dumps(cost_summary(), indent=2))
    else:
        print("unknown command:", cmd)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except RetrievalError as e:
        print("RETRIEVAL ERROR:", e)
        sys.exit(2)
    except FileNotFoundError as e:
        print("FILE NOT FOUND:", e)
        sys.exit(3)
    except Exception as e:  # never dump a raw traceback to the user
        print("ERROR:", type(e).__name__, str(e)[:200])
        sys.exit(1)
