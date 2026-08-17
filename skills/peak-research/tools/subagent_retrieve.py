#!/usr/bin/env python3
"""Leaf retrieval worker — runs ONE pass and prints ONE JSON blob to stdout.

Spawned by run_research.py phase 2, up to 10 concurrent, per the subagent contract
in CONFIG.md: explicit goal, fixed source types, primary findings only, and failures
RECORDED rather than papered over with a substitute source.

This used to be regenerated from a string literal inside run_research.py on every
run. It is now ordinary checked-in source — one copy, and no write into the install
directory.
"""
import sys, json, os
# Force UTF-8 on stdout: the default Windows console encoding (cp1252) cannot encode
# characters that routinely appear in titles and abstracts, and the resulting
# UnicodeEncodeError kills the retrieval pass after the network call has been paid for.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass
# This file lives IN tools/, so tools/ is the sibling directory — not tools/tools.
# The old path was a no-op that only worked because Python puts the script's own
# directory on sys.path anyway; it broke the moment the worker was invoked by module
# path instead of file path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retrieval as R
import argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True)
    ap.add_argument("--context-file", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    with open(args.context_file, encoding="utf-8") as f:
        ctx = json.load(f)

    records = {}
    pass_type = ctx.get("pass")
    year_from = ctx.get("year_from", "2022-01-01")
    per = ctx.get("per", 5)

    try:
        if pass_type == "discovery":
            query = ctx["query"]
            hits = R.openalex_search(query, year_from, per)
            for h in hits:
                key = h.get("doi") or h.get("id") or query
                records[key] = h

        elif pass_type == "targeted":
            axid = ctx["arxiv_id"]
            r = R.resolve_paper(axid)
            if r:
                records[f"arxiv:{axid}"] = r

        elif pass_type == "contradiction":
            query = ctx["query"]
            hits = R.openalex_search(query, year_from, per)
            for h in hits:
                key = h.get("doi") or h.get("id") or query
                records[key] = h

        elif pass_type == "gap":
            stem = ctx.get("query") or ctx["subquestion"].replace("_", " ")
            hits = R.openalex_search(stem, year_from, per)
            for h in hits:
                key = h.get("doi") or h.get("id") or stem
                records[key] = h

    except R.RetrievalError as e:
        print(json.dumps({"error": str(e), "pass": pass_type}))
        sys.exit(1)

    print(json.dumps({"records": records, "pass": pass_type}, ensure_ascii=False))

if __name__ == "__main__":
    main()
