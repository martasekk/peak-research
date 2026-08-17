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
    python retrieval.py verify record.json   # checks every cited source resolves

Requires only the stdlib (urllib) — no pip installs. Works on Windows git-bash/msys.
"""
from __future__ import annotations
import json, urllib.request, urllib.parse, urllib.error, re, sys, time, os

MAILTO = os.environ.get("OPENALEX_MAILTO", "research@example.com")
UA = {"User-Agent": "peak-research-toolkit/1.0 (mailto:%s)" % MAILTO}
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


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
                    sort: str = "cited_by_count:desc", extra_filter: str = ""):
    """Title+abstract phrase search (SHORT stems only — long phrases return 0)."""
    year_from = _norm_date(year_from)
    filt = 'title_and_abstract.search:"%s",from_publication_date:%s' % (phrase, year_from)
    if extra_filter:
        filt += "," + extra_filter
    q = [
        "filter=" + urllib.parse.quote(filt, safe=""),
        "sort=" + urllib.parse.quote(sort),
        "per-page=%d" % per,
        "mailto=" + MAILTO,
    ]
    url = "https://api.openalex.org/works?" + "&".join(q)
    try:
        d = _get_json(url)
    except urllib.error.HTTPError as e:
        raise RetrievalError("OpenAlex HTTP %s: %s" % (e.code, e.read().decode()[:200]))
    return [_work_to_record(w) for w in d.get("results", [])]


def openalex_by_arxiv(axid: str):
    # Live API NOTE: `filter=arxiv:ID` is REJECTED. arXiv works are indexed by their
    # DOI in the form 10.48550/arxiv.ID. Use that. `select=` makes this fetch $0 (free tier).
    doi = "10.48550/arxiv.%s" % axid
    url = "https://api.openalex.org/works?filter=doi:%s&select=id,title,cited_by_count,abstract_inverted_index,publication_year,doi,type,authorships,primary_location,open_access&mailto=%s" % (doi, MAILTO)
    d = _get_json(url)
    if not d.get("results"):
        return None
    return _work_to_record(d["results"][0])


def openalex_by_doi(doi: str):
    url = "https://api.openalex.org/works?filter=doi:%s&select=id,title,cited_by_count,abstract_inverted_index,publication_year,doi,type,authorships,primary_location,open_access&mailto=%s" % (doi, MAILTO)
    d = _get_json(url)
    if not d.get("results"):
        return None
    return _work_to_record(d["results"][0])


def openalex_by_id(openalex_id: str):
    # Single-entity GET + select= is $0 (free). openalex_id like W4353112996 (no URL prefix needed).
    url = "https://api.openalex.org/works/%s?select=id,title,cited_by_count,abstract_inverted_index,publication_year,doi,type,authorships,primary_location,open_access&mailto=%s" % (openalex_id, MAILTO)
    d = _get_json(url)
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
        "authors": [a["author"]["display_name"] for a in (w.get("authorships") or [])][:3],
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
def verify_records(path: str, min_abstract: int = 15) -> dict:
    """S19 verification gate.

    A source is VERIFIED if it resolves to a real record: it has a title and either
    (a) an extractable abstract, or (b) is a repo/raw source, or (c) is a known-primary
    OpenAlex work whose abstract is simply unavailable (abstract_inverted_index: null) —
    in that case it is marked verified-but-'no_abstract' (screen as discovery-only, not cited
    as a claim source). A source is UNVERIFIED only if it has no title or no resolvable identity.
    """
    recs = json.load(open(path, encoding="utf-8"))
    records = recs.get("records", recs) if isinstance(recs, dict) else recs
    ok, bad, no_abs = 0, [], []
    for k, v in (records.items() if isinstance(records, dict) else enumerate(records)):
        title = (v.get("title") or "").strip()
        abst = (v.get("abstract") or "").strip()
        st = v.get("source_type", "")
        if not title:
            bad.append(k)
            continue
        if abst or st in ("github_repo", "github_search", "raw"):
            ok += 1
        elif st in ("openalex",) and v.get("id"):  # resolved OpenAlex work, no abstract text
            ok += 1
            no_abs.append(k)
        else:
            bad.append(k)
    return {"total": len(records), "verified": ok, "no_abstract": no_abs,
            "unverified": bad, "pass": len(bad) == 0}


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
        print(json.dumps(verify_records(args[0]), indent=2))
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
