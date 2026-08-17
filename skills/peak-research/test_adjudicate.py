"""Tests for the optional LLM adjudication layer.

Runs WITHOUT the anthropic SDK and without network by injecting a stub module
into sys.modules. What this verifies is the code we own: batching, index
mapping, prompt assembly, schema conformance, response parsing, disk caching,
cost logging, refusal/error handling, and — most importantly — that every
failure path degrades to the heuristics instead of crashing a run.

What it does NOT verify: that the model's judgments are good. That needs a
labelled sample and a live key; see CHANGELOG.md "Not addressed".
"""
import sys, os, json, types, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, HERE)

# Start from a known-empty adjudication config. Without this the suite inherits
# whatever the machine has configured — and once the plugin IS configured (a
# PEAK_ADJUDICATION_PROVIDER=nvidia in settings.json env, say) the anthropic-stub
# sections below fail, because provider() resolves to openai and never looks at
# the stub SDK. The doctor command runs these tests on exactly such a machine,
# so a suite that only passes on an unconfigured box is worse than no suite.
for _v in ("PEAK_ADJUDICATION_PROVIDER", "PEAK_ADJUDICATION_MODEL",
           "PEAK_ADJUDICATION_MODEL_TYPING", "PEAK_ADJUDICATION_MODEL_CONTRADICTIONS",
           "PEAK_ADJUDICATION_BASE_URL", "PEAK_ADJUDICATION_API_KEY",
           "PEAK_ADJUDICATION_AUTH_HEADER", "PEAK_ADJUDICATION_AUTH_PREFIX",
           "PEAK_ADJUDICATION_EXTRA_HEADERS", "NVIDIA_API_KEY",
           "OPENROUTER_API_KEY", "TOGETHER_API_KEY", "GROQ_API_KEY",
           "OPENAI_API_KEY"):
    os.environ.pop(_v, None)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def section(title):
    print(f"\n=== {title} ===")


# ------------------------------------------------------------------ stub SDK
class _StubError(Exception):
    def __init__(self, msg="", status_code=500):
        super().__init__(msg)
        self.status_code = status_code


class StubMessages:
    """Records requests; replays queued responses."""

    def __init__(self):
        self.requests = []
        self.queue = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.queue.pop(0) if self.queue else {"results": []}
        if isinstance(item, Exception):
            raise item
        block = types.SimpleNamespace(type="text", text=json.dumps(item))
        return types.SimpleNamespace(
            content=[block], stop_reason="end_turn", stop_details=None)


class StubClient:
    def __init__(self, *a, **kw):
        self.messages = SHARED_MESSAGES


SHARED_MESSAGES = StubMessages()
stub = types.ModuleType("anthropic")
stub.Anthropic = StubClient
stub.NotFoundError = type("NotFoundError", (_StubError,), {})
stub.RateLimitError = type("RateLimitError", (_StubError,), {})
stub.APIStatusError = type("APIStatusError", (_StubError,), {})
stub.APIConnectionError = type("APIConnectionError", (_StubError,), {})
stub.__version__ = "stub"
sys.modules["anthropic"] = stub
os.environ["ANTHROPIC_API_KEY"] = "stub-key-for-tests"

import adjudicate as ADJ

# Isolate the cache so tests never read or write the real one.
_TMP = tempfile.mkdtemp(prefix="peak_adj_test_")
ADJ.R.CACHE_DIR = _TMP
ADJ.R.COST_LOG = os.path.join(_TMP, "cost_log.json")


def reset():
    SHARED_MESSAGES.requests.clear()
    SHARED_MESSAGES.queue.clear()
    for f in os.listdir(_TMP):
        os.remove(os.path.join(_TMP, f))


CLAIMS = [
    {"claim": "We find that a high first offer increases the final agreed price.",
     "type": "untyped", "type_confidence": "low", "source_id": "S1",
     "source_doi": "10.1/a", "claim_idx": 0},
    {"claim": "All code and data are available at the project repository page.",
     "type": "derived", "type_confidence": "heuristic", "source_id": "S1",
     "source_doi": "10.1/a", "claim_idx": 1},
]


# --------------------------------------------------------------- availability
section("Availability gating")

check("available() true with stub SDK + key", ADJ.available()[0] is True)

_key = os.environ.pop("ANTHROPIC_API_KEY")
ok_nokey, reason_nokey = ADJ.available()
os.environ["ANTHROPIC_API_KEY"] = _key
# A profile directory on this machine can legitimately satisfy the check.
check("available() explains itself when it says no",
      ok_nokey is True or "credential" in reason_nokey or "API_KEY" in reason_nokey,
      f"-- {reason_nokey}")

sys.modules["anthropic"] = None
try:
    ok_nosdk, reason_nosdk = ADJ.available()
finally:
    sys.modules["anthropic"] = stub
check("missing SDK is reported, not raised",
      ok_nosdk is False and "install" in reason_nosdk, f"-- {reason_nosdk}")


# ---------------------------------------------------------------- claim typing
section("Claim typing")
reset()
SHARED_MESSAGES.queue.append({"results": [
    {"index": 0, "substantive": True, "type": "fact", "confidence": "high",
     "reason": "authors report a measured effect"},
    {"index": 1, "substantive": False, "type": "untyped", "confidence": "high",
     "reason": "data availability statement"},
]})
typed = ADJ.type_claims(CLAIMS, "B2B negotiation")

check("returns one entry per input claim", typed is not None and len(typed) == 2)
check("type is overwritten by the adjudication", typed[0]["type"] == "fact")
check("confidence is namespaced so its origin is visible",
      typed[0]["type_confidence"] == "llm-high", f"-- {typed[0]['type_confidence']}")
check("the model's reason is retained", "measured" in typed[0]["type_cue"])
check("non-substantive sentence is flagged", typed[1]["substantive"] is False)
check("substantive sentence is flagged", typed[0]["substantive"] is True)
check("input claims are not mutated in place",
      CLAIMS[0]["type"] == "untyped" and "substantive" not in CLAIMS[0],
      "-- caller's ledger must be left alone")

req = SHARED_MESSAGES.requests[0]
check("uses the configured model", req["model"] == ADJ.MODEL)
check("requests structured output",
      req["output_config"]["format"]["type"] == "json_schema")
check("effort is set alongside format in output_config",
      req["output_config"].get("effort") == "medium")
check("system prompt is cached (identical across batches)",
      req["system"][0]["cache_control"]["type"] == "ephemeral")
check("schema forbids extra properties",
      req["output_config"]["format"]["schema"]["additionalProperties"] is False)
check("topic reaches the prompt", "B2B negotiation" in req["messages"][0]["content"])
check("claim text reaches the prompt", "first offer" in req["messages"][0]["content"])


# ----------------------------------------------------------------- batching
section("Batching and index mapping")
reset()
many = [dict(CLAIMS[0], claim=f"Sentence number {i} reports a measured result.",
             source_id=f"S{i}", source_doi=f"10.1/{i}")
        for i in range(ADJ.CLAIMS_PER_CALL + 5)]
for batch_start in (0, ADJ.CLAIMS_PER_CALL):
    size = min(ADJ.CLAIMS_PER_CALL, len(many) - batch_start)
    SHARED_MESSAGES.queue.append({"results": [
        {"index": i, "substantive": True, "type": "fact", "confidence": "medium",
         "reason": "measured"} for i in range(size)]})
out = ADJ.type_claims(many, "t")
check("splits into batches of CLAIMS_PER_CALL", len(SHARED_MESSAGES.requests) == 2,
      f"-- {len(SHARED_MESSAGES.requests)} requests")
check("every claim across batches is typed",
      all(c["type"] == "fact" for c in out), "-- second batch indices misapplied")
check("second batch prompt starts renumbering at 0",
      "\n0. " in SHARED_MESSAGES.requests[1]["messages"][0]["content"]
      or SHARED_MESSAGES.requests[1]["messages"][0]["content"].count("0. ") > 0)

reset()
SHARED_MESSAGES.queue.append({"results": [
    {"index": 99, "substantive": True, "type": "fact", "confidence": "high",
     "reason": "x"},
    {"index": -1, "substantive": True, "type": "fact", "confidence": "high",
     "reason": "x"},
    {"index": "0", "substantive": True, "type": "fact", "confidence": "high",
     "reason": "x"},
]})
safe = ADJ.type_claims(CLAIMS, "t")
check("out-of-range and non-integer indices are ignored, not crashed",
      safe is not None and safe[0]["type"] == "untyped")


# -------------------------------------------------------------------- caching
section("Caching and cost")
reset()
SHARED_MESSAGES.queue.append({"results": [
    {"index": 0, "substantive": True, "type": "fact", "confidence": "high", "reason": "r"},
    {"index": 1, "substantive": True, "type": "fact", "confidence": "high", "reason": "r"},
]})
ADJ.type_claims(CLAIMS, "topic-A")
calls_after_first = len(SHARED_MESSAGES.requests)
ADJ.type_claims(CLAIMS, "topic-A")          # identical -> must hit disk cache
check("identical request is served from cache",
      len(SHARED_MESSAGES.requests) == calls_after_first,
      "-- re-running the same corpus must cost $0")

SHARED_MESSAGES.queue.append({"results": []})
ADJ.type_claims(CLAIMS, "topic-B")          # different topic -> must NOT hit cache
check("different prompt misses the cache",
      len(SHARED_MESSAGES.requests) > calls_after_first)

cost = json.loads(open(ADJ.R.COST_LOG, encoding="utf-8").read())
check("spend is recorded in the shared cost log", cost.get("total_usd", 0) > 0,
      f"-- {cost}")


# ------------------------------------------------------- failure degradation
section("Failure degradation (never crash a run)")

for label, err in [
    ("rate limit", stub.RateLimitError("429")),
    ("API error", stub.APIStatusError("500")),
    ("connection failure", stub.APIConnectionError("no route")),
    ("model not found", stub.NotFoundError("bad model")),
]:
    reset()
    SHARED_MESSAGES.queue.append(err)
    check(f"{label} degrades to None (caller keeps heuristics)",
          ADJ.type_claims(CLAIMS, "t") is None)

reset()
SHARED_MESSAGES.queue.append({"results": []})
orig = StubMessages.create


def refusing(self, **kwargs):
    self.requests.append(kwargs)
    return types.SimpleNamespace(
        content=[], stop_reason="refusal",
        stop_details=types.SimpleNamespace(category="cyber", explanation="x"))


StubMessages.create = refusing
check("a safety refusal degrades instead of reading empty content",
      ADJ.type_claims(CLAIMS, "t") is None)


def truncating(self, **kwargs):
    self.requests.append(kwargs)
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text='{"resu')],
        stop_reason="max_tokens", stop_details=None)


StubMessages.create = truncating
reset()
check("a truncated response degrades instead of parsing partial JSON",
      ADJ.type_claims(CLAIMS, "t") is None)


def bad_json(self, **kwargs):
    self.requests.append(kwargs)
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="not json at all")],
        stop_reason="end_turn", stop_details=None)


StubMessages.create = bad_json
reset()
check("unparseable JSON degrades", ADJ.type_claims(CLAIMS, "t") is None)
StubMessages.create = orig

check("empty claim list short-circuits without a call",
      ADJ.type_claims([], "t") is None)


# ------------------------------------------------------------- contradictions
section("Contradiction adjudication")
reset()
CANDIDATES = [
    {"topic": "anchoring", "cluster_size": 2, "type": "candidate",
     "status": "unresolved", "claims_for": ["Anchoring raises the final price."],
     "claims_against": ["Anchoring lowers the final price."],
     "sources_for": ["Paper A (2023)"], "sources_against": ["Paper B (2024)"],
     "resolution": "check first"},
    {"topic": "framing", "cluster_size": 2, "type": "candidate",
     "status": "unresolved", "claims_for": ["Framing helps."],
     "claims_against": ["Framing hurts."],
     "sources_for": ["Paper C (2023)"], "sources_against": ["Paper D (2024)"],
     "resolution": "check first"},
]
SHARED_MESSAGES.queue.append({"results": [
    {"index": 0, "verdict": "method-mismatch", "confidence": "medium",
     "explanation": "different price measures: list vs transacted",
     "what_would_settle_it": "re-analyse both on transacted price"},
    {"index": 1, "verdict": "not-a-conflict", "confidence": "high",
     "explanation": "detector misfired", "what_would_settle_it": "n/a"},
]})
verdicts = ADJ.adjudicate_contradictions(CANDIDATES, "B2B negotiation")

check("non-conflicts are dropped", len(verdicts) == 1, f"-- {len(verdicts)}")
check("verdict replaces the placeholder type",
      verdicts[0]["type"] == "method-mismatch")
check("confidence is surfaced in the status",
      "medium" in verdicts[0]["status"], f"-- {verdicts[0]['status']}")
check("explanation replaces the boilerplate resolution",
      "transacted" in verdicts[0]["resolution"])
check("what-would-settle-it is captured",
      "re-analyse" in verdicts[0]["what_would_settle_it"])
check("adjudicated items are marked as such",
      verdicts[0]["verification"] == "llm-adjudicated")
check("input candidates are not mutated",
      CANDIDATES[0]["type"] == "candidate")

creq = SHARED_MESSAGES.requests[0]
check("contradiction triage runs at higher effort than typing",
      creq["output_config"]["effort"] == "high")
check("both sides reach the prompt",
      "raises the final price" in creq["messages"][0]["content"]
      and "lowers the final price" in creq["messages"][0]["content"])
check("source attributions reach the prompt",
      "Paper A (2023)" in creq["messages"][0]["content"])

reset()
SHARED_MESSAGES.queue.append(stub.RateLimitError("429"))
check("contradiction failure degrades to None",
      ADJ.adjudicate_contradictions(CANDIDATES, "t") is None)
check("no candidates short-circuits", ADJ.adjudicate_contradictions([], "t") is None)


# --------------------------------------------------------------- integration
section("Pipeline integration")

import run_research as RR
src = open(os.path.join(HERE, "run_research.py"), encoding="utf-8").read()
check("adjudication is opt-in via a flag", "--adjudicate" in src)
check("phase 3 threads the flag through", "adjudicate=args.adjudicate" in src)
check("artifact distinguishes adjudicated from lexical contradictions",
      "llm-adjudicated" in src and "Run with `--adjudicate`" in src)
check("heuristic path still exists as the fallback",
      "def classify_claim_type" in src and "def detect_contradictions" in src)

# ------------------------------------------------------ provider selection
section("Provider selection")

_saved = {k: os.environ.get(k) for k in
          ("ANTHROPIC_API_KEY", "NVIDIA_API_KEY", "PEAK_ADJUDICATION_PROVIDER",
           "PEAK_ADJUDICATION_MODEL", "PEAK_ADJUDICATION_MODEL_TYPING",
           "PEAK_ADJUDICATION_MODEL_CONTRADICTIONS", "PEAK_ADJUDICATION_BASE_URL",
           "PEAK_ADJUDICATION_API_KEY", "PEAK_ADJUDICATION_AUTH_HEADER",
           "PEAK_ADJUDICATION_AUTH_PREFIX", "PEAK_ADJUDICATION_EXTRA_HEADERS",
           "OPENAI_API_KEY")}


def setenv(**kw):
    for k in _saved:
        os.environ.pop(k, None)
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = v


def restore():
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


setenv()
check("no keys at all infers anthropic (does not silently pick a paid API)",
      ADJ.provider() == "anthropic", f"-- {ADJ.provider()}")

setenv(NVIDIA_API_KEY="nvapi-x")
check("an NVIDIA key infers the openai-compatible transport",
      ADJ.provider() == "openai")
check("base_url defaults to NVIDIA's endpoint for an NVIDIA key",
      "integrate.api.nvidia.com" in ADJ._openai_base_url(),
      f"-- {ADJ._openai_base_url()}")
check("model defaults to a Nemotron id for that provider",
      "nemotron" in ADJ.model().lower(), f"-- {ADJ.model()}")

for alias in ("nvidia", "nim", "nemotron", "openai-compatible", "vllm", "ollama"):
    setenv(PEAK_ADJUDICATION_PROVIDER=alias, NVIDIA_API_KEY="nvapi-x")
    check(f"provider alias {alias!r} selects the openai transport",
          ADJ.provider() == "openai")

setenv(PEAK_ADJUDICATION_PROVIDER="claude")
check("alias 'claude' selects anthropic", ADJ.provider() == "anthropic")

setenv(PEAK_ADJUDICATION_PROVIDER="gemini")
try:
    ADJ.provider()
    check("unknown provider is rejected", False, "-- no error raised")
except ADJ.AdjudicationUnavailable as e:
    check("unknown provider is rejected with the valid list",
          "nvidia" in str(e) and "anthropic" in str(e))

setenv(ANTHROPIC_API_KEY="sk-ant-x", NVIDIA_API_KEY="nvapi-x")
check("an explicit anthropic key wins over an NVIDIA key when both are set",
      ADJ.provider() == "anthropic")

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", PEAK_ADJUDICATION_API_KEY="k",
       PEAK_ADJUDICATION_BASE_URL="https://gw.example/v1/")
check("explicit base_url wins and loses its trailing slash",
      ADJ._openai_base_url() == "https://gw.example/v1")


# ------------------------------------------------------------ auth headers
section("Auth headers")

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="nvapi-secret")
h = ADJ._auth_headers("nvapi-secret")
check("default is the OpenAI-wire bearer header (what NVIDIA expects)",
      h == {"Authorization": "Bearer nvapi-secret"}, f"-- {h}")

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="k",
       PEAK_ADJUDICATION_AUTH_HEADER="x-api-key")
h = ADJ._auth_headers("k")
check("a custom header name drops the Bearer prefix automatically",
      h == {"x-api-key": "k"}, f"-- {h}")

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="k",
       PEAK_ADJUDICATION_AUTH_PREFIX="")
check("an empty prefix sends the raw key on Authorization",
      ADJ._auth_headers("k") == {"Authorization": "k"})

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="k",
       PEAK_ADJUDICATION_EXTRA_HEADERS='{"X-Org": "acme"}')
check("extra headers are merged", ADJ._auth_headers("k").get("X-Org") == "acme")

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="k",
       PEAK_ADJUDICATION_EXTRA_HEADERS="not json")
try:
    ADJ._auth_headers("k")
    check("malformed extra headers are rejected", False, "-- no error")
except ADJ.AdjudicationUnavailable:
    check("malformed extra headers are rejected clearly", True)


# --------------------------------------------- messy output from any server
section("JSON extraction (unenforced schemas)")

CASES = [
    ("plain object", '{"results": [{"index": 0}]}'),
    ("markdown fence", '```json\n{"results": [{"index": 0}]}\n```'),
    ("bare fence", '```\n{"results": [{"index": 0}]}\n```'),
    ("prose preamble", 'Here is the analysis:\n{"results": [{"index": 0}]}'),
    ("prose on both sides",
     'Sure!\n{"results": [{"index": 0}]}\nLet me know if you need more.'),
    ("reasoning trace",
     '<think>Maybe {"results": [{"index": 99}]} would be wrong.</think>\n'
     '{"results": [{"index": 0}]}'),
    ("reasoning trace + fence",
     '<thinking>hmm</thinking>```json\n{"results": [{"index": 0}]}\n```'),
    ("unclosed reasoning trace",
     '<think>reasoning that never closes\n{"results": [{"index": 0}]}'),
    ("nested braces in strings",
     '{"results": [{"index": 0, "reason": "uses {braces} and \\"quotes\\""}]}'),
]
for label, raw in CASES:
    try:
        got = ADJ.extract_json(raw)
        ok_case = got.get("results", [{}])[0].get("index") == 0
    except Exception as e:
        ok_case, got = False, e
    check(f"extracts JSON from: {label}", ok_case, f"-- {got}")

for label, raw in [("empty string", ""), ("whitespace only", "   \n "),
                   ("no JSON at all", "I cannot help with that."),
                   ("truncated object", '{"results": [{"index": 0')]:
    try:
        ADJ.extract_json(raw)
        check(f"rejects {label}", False, "-- returned without error")
    except ADJ.AdjudicationUnavailable:
        check(f"rejects {label} with a clear error", True)

REQ = ("index", "substantive", "type", "confidence")
good_row = {"index": 0, "substantive": True, "type": "fact", "confidence": "high"}
res = ADJ.coerce_results({"results": [good_row, {"index": 1}, "junk", None]}, REQ)
check("malformed rows are dropped, not fatal", len(res["results"]) == 1)
check("dropped rows are counted for reporting", res["dropped"] == 3)

try:
    ADJ.coerce_results({"nope": []}, REQ)
    check("a response with no results list is rejected", False)
except ADJ.AdjudicationUnavailable:
    check("a response with no results list is rejected", True)

try:
    ADJ.coerce_results({"results": [{"index": 1}, {"index": 2}]}, REQ)
    check("an all-malformed batch is rejected rather than silently empty", False)
except ADJ.AdjudicationUnavailable:
    check("an all-malformed batch is rejected rather than silently empty", True)


# ------------------------------------- out-of-vocabulary labels (no schema)
section("Out-of-vocabulary labels")
setenv(ANTHROPIC_API_KEY="stub-key-for-tests")
reset()
SHARED_MESSAGES.queue.append({"results": [
    {"index": 0, "substantive": True, "type": "empirical-finding",
     "confidence": "high", "reason": "invented type"},
    {"index": 1, "substantive": True, "type": "fact",
     "confidence": "very-sure", "reason": "invented confidence"},
]})
oov = ADJ.type_claims(CLAIMS, "t")
check("an invented claim type is ignored, heuristic value kept",
      oov[0]["type"] == "untyped", f"-- {oov[0]['type']}")
check("an invented confidence is ignored, heuristic value kept",
      oov[1]["type_confidence"] == "heuristic", f"-- {oov[1]['type_confidence']}")

reset()
SHARED_MESSAGES.queue.append({"results": [
    {"index": 0, "verdict": "definitely-a-conflict", "confidence": "high",
     "explanation": "x", "what_would_settle_it": "y"}]})
check("an invented verdict is dropped rather than published",
      ADJ.adjudicate_contradictions(CANDIDATES[:1], "t") == [])

reset()
SHARED_MESSAGES.queue.append({"results": [
    {"index": 0, "verdict": "genuine-empirical", "confidence": "low",
     "explanation": "same measure, opposite signs"}]})   # no what_would_settle_it
partial = ADJ.adjudicate_contradictions(CANDIDATES[:1], "t")
check("a missing optional field falls back instead of raising KeyError",
      partial and partial[0]["what_would_settle_it"] == "unstated")

# ------------------------------------------------- openai transport & modes
section("OpenAI-compatible transport")

_real_post = ADJ._post_json
POSTS = []


def fake_post(reject_formats=(), reply=None, http=None):
    """Simulate a server that rejects some response_format modes."""
    def _post(url, payload, headers, timeout=180):
        POSTS.append({"url": url, "payload": payload, "headers": headers})
        rf = (payload.get("response_format") or {}).get("type")
        if http:
            raise ADJ.AdjudicationUnavailable(http)
        if rf in reject_formats:
            raise ADJ.AdjudicationUnavailable("HTTP 400 from %s: unsupported "
                                              "response_format %r" % (url, rf))
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": reply or '{"results": []}'}}]}
    return _post


setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="nvapi-secret",
       PEAK_ADJUDICATION_MODEL="nvidia/nemotron-test")
reset()
POSTS.clear()
ADJ._post_json = fake_post()
ADJ._call_openai("sys", "usr", {"type": "object"}, "medium")
p = POSTS[0]
check("posts to /chat/completions on the configured base_url",
      p["url"] == "https://integrate.api.nvidia.com/v1/chat/completions", f"-- {p['url']}")
check("sends the configured model", p["payload"]["model"] == "nvidia/nemotron-test")
check("sends the bearer token", p["headers"]["Authorization"] == "Bearer nvapi-secret")
check("system prompt goes in a system message",
      p["payload"]["messages"][0]["role"] == "system")
check("schema is repeated in the user message for servers that ignore the param",
      "schema" in p["payload"]["messages"][1]["content"].lower())
check("first attempt asks for strict json_schema",
      p["payload"]["response_format"]["type"] == "json_schema"
      and p["payload"]["response_format"]["json_schema"]["strict"] is True)
check("temperature is low (classification, not prose)",
      p["payload"]["temperature"] <= 0.3)

POSTS.clear()
ADJ._post_json = fake_post(reject_formats=("json_schema",))
ADJ._call_openai("sys", "usr", {"type": "object"}, "medium")
check("a server rejecting json_schema falls back to json_object",
      len(POSTS) == 2 and POSTS[1]["payload"]["response_format"]["type"] == "json_object",
      f"-- {len(POSTS)} attempts")

POSTS.clear()
ADJ._post_json = fake_post(reject_formats=("json_schema", "json_object"))
ADJ._call_openai("sys", "usr", {"type": "object"}, "medium")
check("a server rejecting both formats falls back to no format hint",
      len(POSTS) == 3 and "response_format" not in POSTS[2]["payload"],
      f"-- {len(POSTS)} attempts")

for code, label in [("HTTP 401", "bad key"), ("HTTP 403", "forbidden"),
                    ("HTTP 404", "wrong model or base_url")]:
    POSTS.clear()
    ADJ._post_json = fake_post(http="%s from x: nope" % code)
    try:
        ADJ._call_openai("sys", "usr", {"type": "object"}, "medium")
        check(f"{code} ({label}) stops immediately", False, "-- no error raised")
    except ADJ.AdjudicationUnavailable:
        check(f"{code} ({label}) stops immediately instead of retrying formats",
              len(POSTS) == 1, f"-- {len(POSTS)} attempts wasted")

for fr, label in [("length", "token limit"), ("content_filter", "filtered")]:
    ADJ._post_json = lambda url, payload, headers, timeout=180, _fr=fr: {
        "choices": [{"finish_reason": _fr, "message": {"content": ""}}]}
    try:
        ADJ._call_openai("sys", "usr", {"type": "object"}, "medium")
        check(f"finish_reason={fr} ({label}) is caught", False)
    except ADJ.AdjudicationUnavailable:
        check(f"finish_reason={fr} ({label}) is caught, not parsed as JSON", True)

ADJ._post_json = lambda url, payload, headers, timeout=180: {
    "choices": [{"finish_reason": "stop",
                 "message": {"content": "", "reasoning_content": "I thought a lot"}}]}
try:
    ADJ._call_openai("sys", "usr", {"type": "object"}, "medium")
    check("a reasoning-only response is caught", False)
except ADJ.AdjudicationUnavailable as e:
    check("a reasoning-only response is caught with a useful message",
          "reasoning" in str(e))

ADJ._post_json = lambda url, payload, headers, timeout=180: {"unexpected": True}
try:
    ADJ._call_openai("sys", "usr", {"type": "object"}, "medium")
    check("an unexpected response shape is caught", False)
except ADJ.AdjudicationUnavailable:
    check("an unexpected response shape is caught, not IndexError", True)

# End-to-end through the public entry point on the openai provider.
reset()
POSTS.clear()
ADJ._post_json = fake_post(reject_formats=("json_schema",), reply=(
    '<think>Let me consider each sentence.</think>\n'
    '```json\n{"results": [\n'
    ' {"index": 0, "substantive": true, "type": "fact", "confidence": "high",'
    '  "reason": "reports a measured effect"},\n'
    ' {"index": 1, "substantive": false, "type": "untyped", "confidence": "high",'
    '  "reason": "availability statement"}\n]}\n```'))
e2e = ADJ.type_claims(CLAIMS, "negotiation")
check("full path works against a messy non-strict server",
      e2e is not None and e2e[0]["type"] == "fact"
      and e2e[1]["substantive"] is False, f"-- {e2e}")
check("provenance still records the LLM origin",
      e2e[0]["type_confidence"] == "llm-high")
check("no anthropic SDK call was made on the openai path",
      len(SHARED_MESSAGES.requests) == 0)

ADJ._post_json = _real_post
restore()


# ------------------------------------------------- model chain and fallback
section("Model chain / fallback")

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="nvapi-x",
       PEAK_ADJUDICATION_MODEL="a/one, b/two ,a/one, c/three")
check("chain splits on commas and trims", ADJ.models() == ["a/one", "b/two", "c/three"],
      f"-- {ADJ.models()}")
check("duplicates are dropped (a dead model must not be retried)",
      ADJ.models().count("a/one") == 1)
check("model() is the head of the chain", ADJ.model() == "a/one")

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="nvapi-x",
       PEAK_ADJUDICATION_MODEL="base/m",
       PEAK_ADJUDICATION_MODEL_TYPING="fast/m",
       PEAK_ADJUDICATION_MODEL_CONTRADICTIONS="deep/m,base/m")
check("per-task override wins for typing", ADJ.models("typing") == ["fast/m"])
check("per-task override wins for contradictions",
      ADJ.models("contradictions") == ["deep/m", "base/m"])
check("unknown task falls through to the global chain",
      ADJ.models("nonesuch") == ["base/m"])
check("no task falls through to the global chain", ADJ.models() == ["base/m"])

setenv(PEAK_ADJUDICATION_PROVIDER="nvidia", NVIDIA_API_KEY="nvapi-x",
       PEAK_ADJUDICATION_MODEL="dead/one,live/two")
reset()
_seen = []


def _chain_post(url, payload, headers, timeout=180):
    _seen.append(payload["model"])
    if payload["model"] == "dead/one":
        raise ADJ.AdjudicationUnavailable("HTTP 404 from %s: model not found" % url)
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
        {"results": [{"index": 0, "substantive": True, "type": "fact",
                      "confidence": "high", "reason": "measured"}]})}}]}


_real_post = ADJ._post_json
ADJ._post_json = _chain_post
out = ADJ.type_claims(CLAIMS[:1], "topic")
check("a 404 on the first model falls through to the second",
      out is not None and out[0]["type"] == "fact", f"-- {out}")
check("both models were attempted, in order", _seen[:1] == ["dead/one"]
      and "live/two" in _seen, f"-- {_seen}")

# The winning model's answer is cached under ITS key, so a rerun must not
# re-attempt the dead model.
_seen.clear()
out2 = ADJ.type_claims(CLAIMS[:1], "topic")
check("rerun is served from cache without retrying the dead model",
      out2 is not None and _seen == [], f"-- {_seen}")

# A credential failure must abort the chain instead of burning every model.
reset()
_seen.clear()


def _auth_fail(url, payload, headers, timeout=180):
    _seen.append(payload["model"])
    raise ADJ.AdjudicationUnavailable("HTTP 401 from %s: unauthorized" % url)


ADJ._post_json = _auth_fail
out3 = ADJ.type_claims(CLAIMS[:1], "topic")
check("401 stops the chain after one model, not len(chain)",
      out3 is None and _seen == ["dead/one"], f"-- {_seen}")

ADJ._post_json = _real_post
restore()

for f in os.listdir(_TMP):
    os.remove(os.path.join(_TMP, f))
os.rmdir(_TMP)

print("\n" + "=" * 60)
if FAILURES:
    print(f"{len(FAILURES)} TEST(S) FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
print("ALL ADJUDICATION TESTS PASS")
