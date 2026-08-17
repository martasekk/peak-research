"""peak-research-v2 gate & regression tests.

Every test here fails against the pre-fix code. Run with:
    python test_gate.py

No network required.
"""
import sys, os, json, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, HERE)
import retrieval as R
import run_research as RR

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- G3: verify_records
section("G3 verify_records")

bad = {"records": {
    "W_REAL": {"source_type": "openalex", "id": "https://openalex.org/W4353112996",
               "title": "Reflexion: Language Agents with Verbal Reinforcement Learning",
               "doi": "10.48550/arxiv.2303.11366",
               "abstract": "We propose Reflexion, a framework that reinforces language agents "
                           "through linguistic feedback stored in an episodic memory buffer."},
    "W_PHANTOM": {"source_type": "openalex", "id": None, "title": "", "abstract": ""},
    "W_NO_IDENTITY": {"source_type": "openalex", "title": "A Paper With No Resolvable ID",
                      "abstract": "x" * 200},
}}
p = os.path.join(tempfile.gettempdir(), "peak_negative_test.json")
json.dump(bad, open(p, "w"))
res = R.verify_records(p)
keys = [u["key"] for u in res["unverified"]]
check("phantom (no title) rejected", "W_PHANTOM" in keys)
check("record with no resolvable identity rejected", "W_NO_IDENTITY" in keys,
      "-- a title alone must not be enough to cite")
check("gate fails overall", res["pass"] is False)

short_abs = {"records": {"W1": {"source_type": "openalex", "id": "https://openalex.org/W1",
                                "title": "Real Paper", "abstract": "too short"}}}
json.dump(short_abs, open(p, "w"))
r_short = R.verify_records(p)
check("sub-threshold abstract marked non-citable", r_short["citable"] == 0,
      f"-- citable={r_short['citable']}")

good = {"records": {
    "W1": {"source_type": "openalex", "id": "https://openalex.org/W1", "title": "Real Paper",
           "abstract": "A sufficiently long abstract that actually describes the study, its "
                       "sample, and its measured outcome in enough words to support a claim."},
    "R1": {"source_type": "github_repo", "full_name": "owner/repo", "title": "repo", "abstract": ""},
}}
json.dump(good, open(p, "w"))
r2 = R.verify_records(p)
check("clean file passes", r2["pass"] is True)
check("citable count excludes no-abstract records", r2["citable"] == 2, f"-- {r2['citable']}")

check("title_overlap detects mismatch",
      R.title_overlap("Reflexion: Language Agents", "Melons as Lemons: Consumer Learning") < 0.4)
check("title_overlap accepts same title",
      R.title_overlap("Reflexion: Language Agents with Verbal Reinforcement",
                      "Reflexion language agents with verbal reinforcement learning") > 0.4)
os.remove(p)


# ---------------------------------------------------------------- G5: check_artifact
section("G5 check_artifact")

prose_only = """# Some Report
Our source of truth is that the fact pattern shows a gap in the evidence.
We audit the claim ledger contradiction synthesis subquestion brief.
"""
f_prose = os.path.join(tempfile.gettempdir(), "peak_prose.md")
open(f_prose, "w", encoding="utf-8").write(prose_only)
check("prose containing the keywords does NOT pass",
      R.check_artifact(f_prose)["pass"] is False,
      "-- old gate substring-matched and passed this")

placeholder_doc = """# T
## Brief
x. ## Subquestions
a
## Evidence Matrix
| a | b |
## Claim Ledger
**fact** something. **inference** other.
## Contradiction Analysis
No direct contradictions detected; manual review recommended.
## Gaps
g
## Synthesis
s
## Audit
a
## Sources
- 10.1016/j.jbusres.2023.114144
- 10.1016/j.indmarman.2023.12.013
- https://openalex.org/W1
- https://openalex.org/W2
- https://openalex.org/W3
"""
f_ph = os.path.join(tempfile.gettempdir(), "peak_ph.md")
open(f_ph, "w", encoding="utf-8").write(placeholder_doc)
res_ph = R.check_artifact(f_ph)
check("placeholder contradiction section fails the gate",
      res_ph["pass"] is False and res_ph["placeholders_found"],
      f"-- {res_ph}")

check("too-few-sources fails",
      R.check_artifact(f_prose, min_sources=5)["has_sources"] is False)
os.remove(f_prose); os.remove(f_ph)


# ------------------------------------------------------- REGRESSION: contradiction pass
section("Contradiction detector (regression: was dead code)")

pos, neg = RR._polarity("Anchoring high improves seller outcomes and increases profit.")
check("_polarity reads the text (positive)", pos > neg, f"-- pos={pos} neg={neg}")
pos2, neg2 = RR._polarity("Anchoring high reduces agreement rates and harms the relationship.")
check("_polarity reads the text (negative)", neg2 > pos2, f"-- pos={pos2} neg={neg2}")
pos3, neg3 = RR._polarity("Anchoring does not improve outcomes.")
check("_polarity honours negation", neg3 > pos3, f"-- pos={pos3} neg={neg3}")

ledger = [
    {"claim": "Aggressive anchoring improves negotiated price outcomes for the seller and "
              "increases the final profit margin in wholesale deals.",
     "type": "fact", "source_id": "S1", "source_title": "Paper One", "source_year": 2023,
     "strength": "high"},
    {"claim": "Aggressive anchoring reduces negotiated price outcomes for the seller and "
              "harms the final profit margin in wholesale deals.",
     "type": "fact", "source_id": "S2", "source_title": "Paper Two", "source_year": 2024,
     "strength": "high"},
]
found = RR.detect_contradictions(ledger, ["anchoring", "negotiated", "wholesale"])
check("opposing claims from different sources ARE detected", len(found) >= 1,
      f"-- got {len(found)}; this returned 0 for every input before the fix")

same_dir = [
    dict(ledger[0]),
    {"claim": "Aggressive anchoring improves negotiated price outcomes and increases margin "
              "for sellers across wholesale contexts.",
     "type": "fact", "source_id": "S3", "source_title": "Paper Three", "source_year": 2024,
     "strength": "high"},
]
check("agreeing claims produce no contradiction",
      len(RR.detect_contradictions(same_dir, ["anchoring", "wholesale"])) == 0)

one_source = [dict(ledger[0], source_id="S9"), dict(ledger[1], source_id="S9")]
check("same-paper opposing claims are not a contradiction",
      len(RR.detect_contradictions(one_source, ["anchoring"])) == 0)


# ------------------------------------------------------- REGRESSION: cache key collision
section("Cache keys (regression: 160-char truncation collided)")

base = "https://api.openalex.org/works?search=" + "a" * 200
u1, u2 = base + "&per-page=1", base + "&per-page=25"
check("long URLs differing only in the tail get distinct cache paths",
      R._cache_path("j_" + u1) != R._cache_path("j_" + u2),
      "-- these mapped to the same file before, silently serving the wrong response")


# ------------------------------------------------------- REGRESSION: fulltext cleaner
section("clean_jina_text (regression: greedy DOTALL wiped documents)")

doc = ("Introduction line.\nTimestamp: 2026-01-01\n"
       + "The study found that treatment increased throughput by 12 percent. " * 5)
cleaned = RR.clean_jina_text(doc)
check("content after a 'Timestamp:' line survives",
      "throughput" in cleaned,
      "-- r'(?i)timestamp:.*' with re.DOTALL deleted the rest of the document")
check("the boilerplate line itself is dropped", "Timestamp:" not in cleaned)
check("bot-wall pages are rejected, not cleaned",
      RR.clean_jina_text("Are you a robot? Please confirm you are a human.") == "")


# ------------------------------------------------------- REGRESSION: claim candidacy
section("Claim extraction")

title = "Joint forward contract negotiation: The role of B2B procurement platforms"
check("a paper's own title is not a claim",
      RR.is_claim_candidate(title + ".", title) is False,
      "-- titles were emitted as typed 'derived' claims")
check("a fragment without a verb is not a claim",
      RR.is_claim_candidate("Anchoring, framing, and discount structures in wholesale.", title) is False)
check("an assertive sentence is a claim candidate",
      RR.is_claim_candidate(
          "We find that buyers exposed to a high first offer concede significantly more "
          "value than buyers in the control condition.", title) is True)
check("section headers are rejected",
      RR.is_claim_candidate("References and acknowledgements for the present study follow below here.", title) is False)

check("markdown structure is stripped from claim text",
      RR.sanitize_claim("## Heading\nwith | pipes") == "Heading with / pipes",
      "-- '#' lines became phantom headings and '|' broke the evidence table")
check("author/affiliation blocks are rejected",
      RR.is_claim_candidate(
          "Zhengbao Jiang 1 Frank Xu 1 Luyu Gao 1 Zhiqing Sun Language Technologies "
          "Institute Carnegie Mellon University Sea AI Lab FAIR Meta", title) is False)
for boiler in [
    "All the codes and datasets used in these experiments are available at the "
    "following public repository for full reproducibility.",
    "This work was supported by the National Science Foundation under grant no 12345 "
    "awarded to the first author of the present study.",
    "The authors declare no conflicts of interest regarding the publication and the "
    "funding of this particular research project.",
    "The remainder of this paper is organized as follows and describes each of the "
    "sections that we present in the work below.",
]:
    check(f"paper furniture rejected: {boiler[:34]!r}...",
          RR.is_claim_candidate(boiler, title) is False,
          "-- these are true sentences that assert nothing, and being near-identical "
          "across papers they faked cross-source corroboration")

check("email blocks are rejected",
      RR.is_claim_candidate(
          "Please direct all correspondence about this dataset and its released code "
          "to {zhengbaj,fangzhex}@cs.cmu.edu for further details.", title) is False)

t, cue = RR.classify_claim_type("The results suggest a significant effect of framing.")
check("hedged wording outranks statistical wording", t == "inference", f"-- got {t}")
t2, cue2 = RR.classify_claim_type("Buyers arrived on Tuesday and the warehouse was open.")
check("uncued sentence is 'untyped', not silently 'derived'", t2 == "untyped", f"-- got {t2}")


# ------------------------------------------------------- REGRESSION: relevance scoring
section("Relevance scoring")

profile = RR.build_topic_profile("B2B wholesale negotiation buyer psychology", [])
check("multi-word phrases are retained separately", len(profile["phrases"]) > 0,
      "-- phrases stored in a word-set could never match tokenised text")

short_rec = {"title": "Negotiation in wholesale", "abstract": "Buyer psychology."}
long_rec = {"title": "Negotiation in wholesale",
            "abstract": "Buyer psychology. " + "This paper reports a controlled experiment. " * 40}
check("a longer abstract is not penalised for length",
      RR.score_relevance(long_rec, profile) >= RR.score_relevance(short_rec, profile),
      "-- the old formula divided by the record's own word count")

off_topic = {"title": "Adjuvant chemotherapy in resected pancreatic cancer",
             "abstract": "We report overall survival in a randomised oncology trial."}
check("off-topic paper scores below threshold",
      RR.score_relevance(off_topic, profile) < RR.CONFIG["relevance_threshold"],
      f"-- scored {RR.score_relevance(off_topic, profile):.3f}")


section("Topical anchor (regression: generic expansion admitted off-topic papers)")

rag_profile = RR.build_topic_profile("retrieval augmented generation for legal document review", [])
rag_profile = dict(rag_profile,
                   expanded=["llms", "language", "knowledge", "models", "graph", "dataset",
                             "large", "information", "task", "external"])

point_clouds = {"title": "Dynamic Graph CNN for Learning on Point Clouds",
                "abstract": "We propose a graph based neural network models approach for "
                            "learning on point clouds using a large dataset and a task "
                            "specific language of local geometric information."}
check("paper hitting only expanded terms has no anchor",
      RR.has_topical_anchor(point_clouds, rag_profile) is False,
      "-- 'models/graph/dataset/language' let unrelated papers into the evidence set")

on_topic = {"title": "Retrieval-augmented generation for Brazilian legal documents",
            "abstract": "We evaluate retrieval over a legal corpus."}
check("on-topic paper has an anchor", RR.has_topical_anchor(on_topic, rag_profile) is True)

check("expanded terms weigh less than core terms",
      RR.score_relevance(point_clouds, rag_profile) < RR.score_relevance(on_topic, rag_profile))

_src = open(os.path.join(HERE, "run_research.py"), encoding="utf-8").read()
check("author expansion uses author.id, not a name search",
      "filter=author.id:" in _src,
      "-- search=<display name> matched the name anywhere, including reference lists")
check("no name-based author search remains",
      "works?search={urllib.parse.quote(author)}" not in _src)


# ------------------------------------------------------- topic independence
section("Topic independence")

src = open(os.path.join(HERE, "run_research.py"), encoding="utf-8").read()
for token in ["anchoring vs framing in B2B contexts", "Industrial Marketing Management",
              "nature reviews clinical oncology"]:
    check(f"no hardcoded topic string: {token!r}", token not in src)

q = RR.derive_queries("retrieval augmented generation for legal document review")
check("queries derive from the topic", any("legal" in x or "retrieval" in x for x in q), f"-- {q}")
check("no B2B default leaks in", not any("B2B" in x for x in q), f"-- {q}")


# ----------------------------------------------------------------------------- summary
print("\n" + "=" * 60)
if FAILURES:
    print(f"{len(FAILURES)} TEST(S) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("ALL GATE TESTS PASS")
