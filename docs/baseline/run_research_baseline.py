"""
peak-research-v2/run_research.py
Drives the 5-phase METHOD against live APIs using tools/retrieval.py.
This is the real runner (not a demo): it produces PLAN.md, EVIDENCE_FILE, and runs the G3 audit gate.

Usage:
    python run_research.py --topic "how to optimize deep agentic research" [--queries q1,q2,q3] [--year 2023]
Outputs land next to the skill under ./runs/<slug>/  (keeps the skill dir clean + reproducible).
"""
from __future__ import annotations
import argparse, json, os, sys, time, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))
import retrieval as R

CONFIG = {
    "mailto": os.environ.get("OPENALEX_MAILTO", "research@example.com"),
    "catalog": r"D:\peak-search\curated_research_source_catalog.md",
    "year_from": "2023-01-01",
}

PASS_DISCOVERY = ["agentic deep research", "LLM agent memory", "retrieval augmented generation agent"]


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "research"


def phase1_plan(topic: str, queries: list, rundir: str):
    plan = {
        "objective": topic,
        "archetype": "literature_review",
        "subquestions": ["background", "current_evidence", "alternatives", "risks"],
        "queries": queries,
        "scope": {"year_from": CONFIG["year_from"], "source_pref": "primary>derivative"},
    }
    json.dump(plan, open(os.path.join(rundir, "PLAN.json"), "w"), indent=2)
    return plan


def phase2_retrieve(plan: dict, rundir: str):
    records = {}
    queries = plan["queries"] or PASS_DISCOVERY
    for q in queries:
        try:
            hits = R.openalex_search(q, CONFIG["year_from"], 3)
            for h in hits:
                records[h["id"] or h.get("doi") or q] = h
            print(f"  [discovery] {q} -> {len(hits)}")
        except R.RetrievalError as e:
            print(f"  [discovery] {q} ERROR: {e}")
        time.sleep(0.5)
    # targeted: resolve canonical arxiv IDs if any were named in queries
    for token in queries:
        m = re.search(r"(\d{4}\.\d{4,5})", token)
        if m:
            try:
                r = R.resolve_paper(m.group(1))
                if r:
                    records["arxiv:" + m.group(1)] = r
                    print(f"  [targeted] {m.group(1)} -> {r.get('title','')[:50]}")
            except R.RetrievalError as e:
                print(f"  [targeted] {m.group(1)} ERROR: {e}")
    ev = {"records": records}
    json.dump(ev, open(os.path.join(rundir, "EVIDENCE.json"), "w"), indent=2, ensure_ascii=False)
    return ev


def phase3_extract(ev: dict):
    # minimal: typed ledger skeleton from records (full extraction is agent-driven)
    ledger = []
    for k, v in ev["records"].items():
        ledger.append({"claim_source": k, "type": "fact",
                       "title": v.get("title"), "note": "auto-seeded; agent refines"})
    return ledger


def phase5_audit(rundir: str):
    evp = os.path.join(rundir, "EVIDENCE.json")
    res = R.verify_records(evp)
    json.dump(res, open(os.path.join(rundir, "AUDIT.json"), "w"), indent=2)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--queries", default="", help="comma-sep OpenAlex stems")
    ap.add_argument("--year", default="2023-01-01")
    a = ap.parse_args()
    CONFIG["year_from"] = a.year
    queries = [q.strip() for q in a.queries.split(",") if q.strip()] or None

    rundir = os.path.join(HERE, "runs", slugify(a.topic))
    os.makedirs(rundir, exist_ok=True)
    print(f"== peak-research-v2 run: {a.topic}\n   rundir: {rundir}")

    print("\n[PHASE 1] PLAN")
    plan = phase1_plan(a.topic, queries, rundir)
    print("[PHASE 2] RETRIEVE")
    ev = phase2_retrieve(plan, rundir)
    print(f"  retrieved {len(ev['records'])} records")
    print("[PHASE 3] EXTRACT (skeleton)")
    phase3_extract(ev)
    print("[PHASE 5] AUDIT GATE")
    res = phase5_audit(rundir)
    print(json.dumps(res, indent=2))
    if not res["pass"]:
        print("\n  AUDIT FAILED — unverified sources present. Fix before publishing.")
        sys.exit(2)
    print("\n== v2 pipeline complete; artifact synthesis is agent-driven (METHOD Phase 4). ==")


if __name__ == "__main__":
    main()
