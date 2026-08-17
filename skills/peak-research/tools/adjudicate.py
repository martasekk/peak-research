"""
peak_research/tools/adjudicate.py
=================================
OPTIONAL LLM adjudication layer.

Replaces two lexical heuristics with a model that actually reads the sentences —

  * claim typing        (cue matching -> reads the sentence in context)
  * contradiction pass  (word polarity -> distinguishes a genuine empirical
                         conflict from a method/definition mismatch, which is
                         METHOD step 13's whole point)

Both entry points return None on ANY failure, and `run_research.py` then keeps
its heuristics. Nothing here is required for a run.

TWO PROVIDERS
-------------
  anthropic  (default)  official SDK, guaranteed-valid structured output.
                        pip install anthropic; ANTHROPIC_API_KEY or `ant auth login`.

  openai     any OpenAI-compatible /chat/completions endpoint — NVIDIA NIM,
                        OpenRouter, Together, Groq, vLLM, Ollama. Uses stdlib
                        urllib, so it adds NO dependency.

Selection is explicit via PEAK_ADJUDICATION_PROVIDER, else inferred from
whichever key is set. Example (NVIDIA NIM):

    export PEAK_ADJUDICATION_PROVIDER=nvidia
    export NVIDIA_API_KEY=nvapi-...
    python run_research.py --topic "..." --adjudicate

PEAK_ADJUDICATION_MODEL is optional and takes a comma-separated CHAIN, tried in
order — the benchmarked NVIDIA default is used when it is unset. A 404/429/5xx
steps to the next model; only 401/403 aborts. Per-task chains via
PEAK_ADJUDICATION_MODEL_TYPING / _CONTRADICTIONS. See docs/MODEL_BENCHMARK.md.

    # confirm wiring before spending on a run:
    python tools/adjudicate.py --check

NOTE ON STRUCTURED OUTPUT: the Anthropic path gets schema-valid JSON by
construction. OpenAI-compatible servers vary — some enforce `response_format`
json_schema, some ignore it, some 400 on it, and reasoning models may wrap the
answer in <think> blocks or markdown fences. The openai path therefore degrades
through: strict schema -> plain json_object -> no format hint, and parses
defensively (strip reasoning traces and fences, extract the outermost JSON
object, drop malformed entries rather than failing the batch).

Responses are cached on disk under cache/ keyed by provider + model + exact
prompt, so a re-run of the same corpus costs $0. Spend is recorded in the same
cost log the retrieval toolkit uses (`python tools/retrieval.py cost`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retrieval as R  # cost log + cache dir, stdlib-only

# Known OpenAI-compatible hosts, for a friendlier default when only a vendor key
# is set. Any other compatible endpoint works via PEAK_ADJUDICATION_BASE_URL.
_KNOWN_BASE_URLS = {
    "NVIDIA_API_KEY": "https://integrate.api.nvidia.com/v1",
    "OPENROUTER_API_KEY": "https://openrouter.ai/api/v1",
    "TOGETHER_API_KEY": "https://api.together.xyz/v1",
    "GROQ_API_KEY": "https://api.groq.com/openai/v1",
    "OPENAI_API_KEY": "https://api.openai.com/v1",
}
_DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    # Verified live against GET /v1/models on integrate.api.nvidia.com.
    # A chain, not one id: the first entry that answers wins, and a retired model
    # (404), a rate limit (429) or a server error steps to the next instead of
    # dropping the whole run back to the lexical heuristics.
    #
    # Super leads, not Ultra, and that ordering is measured rather than assumed
    # (docs/MODEL_BENCHMARK.md). On a real 20-claim typing batch Super was 57s to
    # Ultra's 85s and returned 16 high-confidence rows to Ultra's 8. On six
    # known-answer contradiction cases Super scored 5/6 in 22s against Ultra's
    # 3/6 in 39s — and Ultra's two misses were both genuine conflicts it
    # downgraded to method-mismatch, i.e. it suppresses exactly what the
    # mandatory contradiction pass exists to surface. Bigger was worse here.
    "openai": "nvidia/nemotron-3-super-120b-a12b,"
              "nvidia/nemotron-3-ultra-550b-a55b",
}

# Per-task model overrides. The two tasks have genuinely different shapes —
# typing is bulk classification (20 sentences a call), contradiction triage is
# comparative reasoning over few items (6 clusters a call) — so they are allowed
# different chains. Unset falls through to PEAK_ADJUDICATION_MODEL.
_TASK_ENV = {
    "typing": "PEAK_ADJUDICATION_MODEL_TYPING",
    "contradictions": "PEAK_ADJUDICATION_MODEL_CONTRADICTIONS",
}

MAX_TOKENS = 16000
CLAIMS_PER_CALL = 20          # keeps each request well inside the output budget
CLUSTERS_PER_CALL = 6

# Rough per-call estimate for the cost log — deliberately an over-estimate.
# These calls are input-heavy. Override for cheaper providers.
_EST_USD_PER_CALL = float(os.environ.get("PEAK_ADJUDICATION_USD_PER_CALL", "0.02"))


class AdjudicationUnavailable(RuntimeError):
    """Provider unusable, credentials missing, or the call failed."""


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


# "openai" names the WIRE PROTOCOL (POST /chat/completions with a bearer token),
# not the vendor — nothing is sent to OpenAI unless you point base_url there.
# NVIDIA NIM, OpenRouter, Together, Groq, vLLM and Ollama all speak it, so these
# aliases all select the same transport.
_PROVIDER_ALIASES = {
    "anthropic": "anthropic", "claude": "anthropic",
    "openai": "openai", "openai-compatible": "openai", "oai": "openai",
    "nvidia": "openai", "nim": "openai", "nemotron": "openai",
    "openrouter": "openai", "together": "openai", "groq": "openai",
    "vllm": "openai", "ollama": "openai", "local": "openai",
}


def provider() -> str:
    """Which backend to use. Explicit setting wins; otherwise infer from keys."""
    p = _env("PEAK_ADJUDICATION_PROVIDER").lower()
    if p in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[p]
    if p:
        raise AdjudicationUnavailable(
            "PEAK_ADJUDICATION_PROVIDER=%r is not recognised. Use 'anthropic', or "
            "one of %s (all the same OpenAI-compatible transport)."
            % (p, ", ".join(sorted(k for k, v in _PROVIDER_ALIASES.items()
                                   if v == "openai"))))
    if _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    # `_openai_key()` returns a (key, source) TUPLE — a bare truth test on it is
    # always True, since even ("", "") is a non-empty tuple. Index the key.
    if _openai_key()[0]:
        return "openai"
    return "anthropic"


def _openai_key() -> tuple[str, str]:
    """(key, which env var it came from). Empty key means none configured."""
    explicit = _env("PEAK_ADJUDICATION_API_KEY")
    if explicit:
        return explicit, "PEAK_ADJUDICATION_API_KEY"
    for var in _KNOWN_BASE_URLS:
        if _env(var):
            return _env(var), var
    return "", ""


def _auth_headers(key: str) -> dict:
    """Auth header(s) for the OpenAI-compatible call.

    `Authorization: Bearer <key>` is the OpenAI-wire standard and is what
    NVIDIA's hosted endpoint (integrate.api.nvidia.com) expects with an
    `nvapi-...` key — NVIDIA does not use a bespoke header there. Some gateways
    and self-hosted deployments do differ, so both the header name and the value
    prefix are overridable:

        PEAK_ADJUDICATION_AUTH_HEADER=x-api-key
        PEAK_ADJUDICATION_AUTH_PREFIX=          # empty -> raw key, no "Bearer "

    Anything else a gateway needs (org IDs, routing headers) can be added as
    JSON via PEAK_ADJUDICATION_EXTRA_HEADERS.
    """
    name = _env("PEAK_ADJUDICATION_AUTH_HEADER", "Authorization")
    prefix = os.environ.get("PEAK_ADJUDICATION_AUTH_PREFIX")
    if prefix is None:
        prefix = "Bearer " if name.lower() == "authorization" else ""
    headers = {name: prefix + key}

    extra = _env("PEAK_ADJUDICATION_EXTRA_HEADERS")
    if extra:
        try:
            parsed = json.loads(extra)
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object")
            headers.update({str(k): str(v) for k, v in parsed.items()})
        except Exception as e:
            raise AdjudicationUnavailable(
                "PEAK_ADJUDICATION_EXTRA_HEADERS is not a JSON object: %s" % e) from e
    return headers


def _openai_base_url() -> str:
    explicit = _env("PEAK_ADJUDICATION_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    _, var = _openai_key()
    return _KNOWN_BASE_URLS.get(var, _KNOWN_BASE_URLS["NVIDIA_API_KEY"]).rstrip("/")


def models(task: str = "") -> list[str]:
    """Ordered model chain for a task. First entry is preferred.

    Resolution: per-task env -> PEAK_ADJUDICATION_MODEL -> provider default.
    Any of them may be a comma-separated chain.
    """
    raw = ""
    if task and task in _TASK_ENV:
        raw = _env(_TASK_ENV[task])
    raw = raw or _env("PEAK_ADJUDICATION_MODEL") or _DEFAULT_MODELS[provider()]
    chain, seen = [], set()
    for part in raw.split(","):
        m = part.strip()
        if m and m not in seen:       # a duplicate would just retry a dead model
            seen.add(m)
            chain.append(m)
    return chain or [_DEFAULT_MODELS[provider()].split(",")[0]]


def model(task: str = "") -> str:
    """The preferred model — first of the chain."""
    return models(task)[0]


# Back-compat: earlier code and tests read a module-level MODEL constant.
# Resolved defensively. provider() raises on an unrecognised
# PEAK_ADJUDICATION_PROVIDER, and this runs at import — so a single typo in that
# variable used to take down every import of run_research.py, killing runs that
# never asked for adjudication at all. available() reports the same problem
# properly, at the point where it actually matters.
try:
    MODEL = model()
except AdjudicationUnavailable:
    MODEL = ""


def available() -> tuple[bool, str]:
    """(usable, reason). Cheap check — makes no network call."""
    try:
        p = provider()
    except AdjudicationUnavailable as e:
        return False, str(e)

    if p == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, ("anthropic package not installed (pip install anthropic), "
                           "or set PEAK_ADJUDICATION_PROVIDER=openai to use an "
                           "OpenAI-compatible endpoint instead")
        if not (_env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN")
                or os.path.isdir(os.path.expanduser("~/.config/anthropic"))
                or os.path.isdir(os.path.join(_env("APPDATA"), "Anthropic"))):
            return False, "no ANTHROPIC_API_KEY and no `ant auth login` profile on disk"
        return True, "anthropic / %s" % model()

    key, var = _openai_key()
    if not key:
        return False, ("no API key found — set PEAK_ADJUDICATION_API_KEY (or one of: "
                       + ", ".join(_KNOWN_BASE_URLS) + ")")
    return True, "openai-compatible / %s @ %s (key from %s)" % (
        model(), _openai_base_url(), var)


def _anthropic_client():
    try:
        import anthropic
    except ImportError as e:
        raise AdjudicationUnavailable(
            "the `anthropic` package is not installed (pip install anthropic)") from e
    try:
        return anthropic.Anthropic()
    except Exception as e:
        # Constructor raises when no credential source resolves at all.
        raise AdjudicationUnavailable("no Anthropic credentials found: %s" % e) from e


# ----------------------------------------------------------------------------
# Cached, cost-logged call
# ----------------------------------------------------------------------------
def _cache_path(payload: str) -> str:
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]
    R._ensure_cache()  # cache dir is created on first write, not on import
    return os.path.join(R.CACHE_DIR, "llm_%s.json" % digest)


# ----------------------------------------------------------------------------
# Defensive JSON extraction (needed for OpenAI-compatible servers)
# ----------------------------------------------------------------------------
_THINK_RE = re.compile(r"<(think|thinking|reasoning)\b.*?</\1>", re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a chat completion.

    Anthropic's structured output makes this a no-op, but OpenAI-compatible
    servers may return the object wrapped in a reasoning trace, a markdown
    fence, or surrounding prose — and reasoning models like Nemotron routinely
    emit <think> blocks that contain JSON-looking text of their own.
    """
    if not text or not text.strip():
        raise AdjudicationUnavailable("model returned an empty response")

    # Reasoning traces first: they can contain braces that would otherwise win
    # the outermost-object scan below.
    cleaned = _THINK_RE.sub("", text)
    # An unclosed <think> (truncated output) swallows the rest — drop the head.
    if re.search(r"<(think|thinking|reasoning)\b", cleaned, re.I):
        cleaned = re.split(r"<(?:think|thinking|reasoning)\b[^>]*>", cleaned, 1)[-1]

    fenced = _FENCE_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1)

    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Last resort: outermost balanced {...}, respecting strings and escapes.
    start = cleaned.find("{")
    if start == -1:
        raise AdjudicationUnavailable("no JSON object in response: %r" % cleaned[:120])
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(cleaned[start:], start):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str and ch == "{":
            depth += 1
        elif not in_str and ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start:i + 1])
                except json.JSONDecodeError as e:
                    raise AdjudicationUnavailable("malformed JSON: %s" % e) from e
    raise AdjudicationUnavailable("unterminated JSON object (response truncated?)")


def coerce_results(data: dict, required: tuple[str, ...]) -> dict:
    """Keep only well-formed rows.

    Without server-side schema enforcement a model can return the right shape
    for nine rows and something odd for the tenth. Dropping the bad row beats
    failing the batch — the caller treats a missing index as "not adjudicated"
    and leaves that claim's heuristic value alone.
    """
    rows = data.get("results")
    if not isinstance(rows, list):
        raise AdjudicationUnavailable(
            "response has no `results` list (got %s)" % type(rows).__name__)
    good = [r for r in rows
            if isinstance(r, dict) and all(k in r for k in required)]
    if rows and not good:
        raise AdjudicationUnavailable(
            "no row carried the required fields %s" % (required,))
    return {"results": good, "dropped": len(rows) - len(good)}


# ----------------------------------------------------------------------------
# OpenAI-compatible transport (stdlib only)
# ----------------------------------------------------------------------------
def _post_json(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise AdjudicationUnavailable("HTTP %s from %s: %s" % (e.code, url, detail)) from e
    except Exception as e:
        raise AdjudicationUnavailable("request to %s failed: %s" % (url, e)) from e


def _call_openai(system: str, user: str, schema: dict, effort: str,
                 model_id: str = "") -> str:
    """Chat-completions call, degrading through three structured-output modes.

    Servers disagree about `response_format`: NVIDIA NIM supports json_schema
    for some models, json_object for more, and 400s or silently ignores it for
    the rest. Try strictest first and step down on rejection.
    """
    key, _ = _openai_key()
    if not key:
        raise AdjudicationUnavailable("no API key configured for the openai provider")
    url = _openai_base_url() + "/chat/completions"
    headers = {"Content-Type": "application/json",
               "Accept": "application/json"}
    headers.update(_auth_headers(key))

    base = {
        "model": model_id or model(),
        "max_tokens": MAX_TOKENS,
        "temperature": 0.2,   # near-deterministic; this is classification, not prose
        "messages": [
            {"role": "system", "content": system},
            # Belt and braces: the schema is in the prompt too, because a server
            # that ignores response_format still has to be told what to emit.
            {"role": "user", "content":
                user + "\n\nRespond with a single JSON object matching this schema, "
                       "and nothing else — no prose, no markdown fence:\n"
                       + json.dumps(schema)},
        ],
    }
    modes = [
        {"response_format": {"type": "json_schema", "json_schema": {
            "name": "adjudication", "strict": True, "schema": schema}}},
        {"response_format": {"type": "json_object"}},
        {},
    ]

    last = None
    for extra in modes:
        try:
            data = _post_json(url, dict(base, **extra), headers)
        except AdjudicationUnavailable as e:
            last = e
            # A 4xx here usually means "this server doesn't take that
            # response_format" — step down. A 401/403/404 won't improve, so stop.
            msg = str(e)
            if any(code in msg for code in ("HTTP 401", "HTTP 403", "HTTP 404")):
                raise
            continue
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError, TypeError) as e:
            raise AdjudicationUnavailable("unexpected response shape: %s" % e) from e
        finish = choice.get("finish_reason")
        if finish == "length":
            raise AdjudicationUnavailable(
                "response hit the token limit — lower CLAIMS_PER_CALL and retry")
        if finish == "content_filter":
            raise AdjudicationUnavailable("provider content filter declined the request")
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        # Some reasoning models put the trace in a sibling field; ignore it.
        if not text.strip() and msg.get("reasoning_content"):
            raise AdjudicationUnavailable(
                "model returned only a reasoning trace and no answer")
        return text
    raise last or AdjudicationUnavailable("all response_format modes failed")


def _is_credential_error(err: Exception) -> bool:
    """True when no other model would help — the key itself is the problem.

    A 404 is deliberately NOT in here: it means *this model* is gone (exactly
    what killed the first nemotron id configured), so the chain should step to
    the next one rather than abandon the run.
    """
    msg = str(err)
    return ("HTTP 401" in msg or "HTTP 403" in msg
            or "no API key" in msg or "no Anthropic credentials" in msg)


def _cache_key(prov: str, model_id: str, system: str, user: str,
               schema: dict, effort: str) -> str:
    return json.dumps({"p": prov, "m": model_id, "s": system, "u": user,
                       "sch": schema, "e": effort}, sort_keys=True)


def _call(system: str, user: str, schema: dict, effort: str = "medium",
          force: bool = False, required: tuple[str, ...] = (),
          task: str = "") -> dict:
    """One structured-output request, walking the task's model chain.

    Cache is checked for EVERY candidate before any network call, so a corpus
    previously adjudicated by the second model is served from disk instead of
    re-failing the first. Live attempts then run in preference order.
    """
    prov = provider()
    chain = models(task)

    if not force:
        for model_id in chain:
            cp = _cache_path(_cache_key(prov, model_id, system, user, schema, effort))
            if os.path.exists(cp):
                try:
                    return json.loads(open(cp, encoding="utf-8").read())
                except Exception:
                    pass

    last = None
    for i, model_id in enumerate(chain):
        try:
            return _call_one(prov, model_id, system, user, schema, effort, required)
        except AdjudicationUnavailable as e:
            if _is_credential_error(e):
                raise           # every model behind this key fails the same way
            last = e
            if i + 1 < len(chain):
                print("    [adjudicate] %s unavailable (%s) — falling back to %s"
                      % (model_id, str(e)[:120], chain[i + 1]))
    raise last or AdjudicationUnavailable("no model in the chain answered")


def _call_one(prov: str, model_id: str, system: str, user: str, schema: dict,
              effort: str, required: tuple[str, ...]) -> dict:
    """A single model's attempt. Caches under that model's key on success."""
    cp = _cache_path(_cache_key(prov, model_id, system, user, schema, effort))

    if prov == "openai":
        text = _call_openai(system, user, schema, effort, model_id)
        data = coerce_results(extract_json(text), required) if required \
            else extract_json(text)
        if data.get("dropped"):
            print("    [adjudicate] dropped %d malformed row(s) from the response"
                  % data["dropped"])
        try:
            open(cp, "w", encoding="utf-8").write(json.dumps(data))
        except Exception:
            pass
        R._log_cost(_EST_USD_PER_CALL)
        return data

    import anthropic
    client = _anthropic_client()
    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=MAX_TOKENS,
            # The instruction block is identical across batches, so caching it
            # makes every call after the first read the prefix at ~0.1x.
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": effort,
                           "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.NotFoundError as e:
        raise AdjudicationUnavailable("model %r not available: %s" % (model_id, e)) from e
    except anthropic.RateLimitError as e:
        raise AdjudicationUnavailable("rate limited: %s" % e) from e
    except anthropic.APIStatusError as e:
        raise AdjudicationUnavailable("API error %s: %s" % (e.status_code, e)) from e
    except anthropic.APIConnectionError as e:
        raise AdjudicationUnavailable("connection failed: %s" % e) from e

    # Safety classifiers can decline with a 200; content is empty or partial.
    if response.stop_reason == "refusal":
        cat = getattr(response.stop_details, "category", None)
        raise AdjudicationUnavailable("request declined (category=%s)" % cat)
    if response.stop_reason == "max_tokens":
        raise AdjudicationUnavailable(
            "response hit max_tokens — lower CLAIMS_PER_CALL and retry")

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AdjudicationUnavailable("model returned unparseable JSON: %s" % e) from e

    try:
        open(cp, "w", encoding="utf-8").write(json.dumps(data))
    except Exception:
        pass
    R._log_cost(_EST_USD_PER_CALL)
    return data


# ----------------------------------------------------------------------------
# 1. Claim typing
# ----------------------------------------------------------------------------
TYPING_SYSTEM = """\
You are adjudicating sentences auto-extracted from research papers for an \
evidence-first literature review. For each numbered sentence, decide two things.

1. `substantive`: does the sentence make a claim ABOUT THE SUBJECT MATTER?
   false for paper furniture that asserts nothing about the topic — data or code
   availability statements, funding, acknowledgements, conflict-of-interest
   declarations, "the remainder of this paper is organized as follows", section
   headers, author affiliations, figure or table captions.

2. `type`, the claim's epistemic status as the AUTHORS present it:
   - fact           : a specific empirical result the authors report measuring
   - derived        : a definition, framework, model, or synthesis of others' work
   - inference      : an interpretation the authors hedge (suggests, consistent with)
   - hypothesis     : a proposed mechanism or prediction, not yet tested here
   - recommendation : actionable advice for practice or policy
   - untyped        : none of the above fits

Judge what the sentence claims, not which words it contains: "results suggest a
significant effect" is an inference (hedged), not a fact, despite "significant".
A sentence reporting someone else's finding is `derived`, not `fact`.

Set `confidence` to low when the sentence is ambiguous out of context — these
are extracted fragments and you cannot see the surrounding paper. `reason` must
be under 15 words. Be willing to mark things untyped or low-confidence; a wrong
confident label is worse for this pipeline than an honest "unclear"."""

CLAIM_TYPE_VALUES = ("fact", "derived", "inference", "hypothesis",
                     "recommendation", "untyped")
CONFIDENCES = ("high", "medium", "low")
VERDICTS = ("genuine-empirical", "method-mismatch", "scope-mismatch", "not-a-conflict")

TYPING_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "substantive": {"type": "boolean"},
                    "type": {"type": "string",
                             "enum": ["fact", "derived", "inference",
                                      "hypothesis", "recommendation", "untyped"]},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "substantive", "type", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def type_claims(claims: list[dict], topic: str) -> list[dict] | None:
    """Adjudicate claim candidates. Returns claims with LLM fields merged in,
    or None if adjudication is unavailable (caller keeps its heuristics)."""
    ok, _ = available()
    if not ok or not claims:
        return None

    out = [dict(c) for c in claims]
    for start in range(0, len(out), CLAIMS_PER_CALL):
        batch = out[start:start + CLAIMS_PER_CALL]
        listing = "\n".join(
            "%d. %s" % (i, c["claim"]) for i, c in enumerate(batch))
        user = ("Review topic: %s\n\nSentences:\n%s" % (topic, listing))
        try:
            data = _call(TYPING_SYSTEM, user, TYPING_SCHEMA, effort="medium",
                         required=("index", "substantive", "type", "confidence"),
                         task="typing")
        except AdjudicationUnavailable as e:
            print("    [adjudicate] typing batch failed, keeping heuristics: %s" % e)
            return None
        for r in data.get("results", []):
            i = r.get("index")
            if not isinstance(i, int) or not 0 <= i < len(batch):
                continue
            if r["type"] not in CLAIM_TYPE_VALUES or r["confidence"] not in CONFIDENCES:
                continue  # unenforced schema: ignore an out-of-vocabulary label
            batch[i]["substantive"] = bool(r["substantive"])
            batch[i]["type"] = r["type"]
            batch[i]["type_confidence"] = "llm-" + r["confidence"]
            batch[i]["type_cue"] = str(r.get("reason", ""))[:120]
            batch[i]["verification"] = "llm-adjudicated"
    return out


# ----------------------------------------------------------------------------
# 2. Contradiction adjudication
# ----------------------------------------------------------------------------
CONTRADICTION_SYSTEM = """\
You are performing the contradiction triage step of an evidence-first review.

Each group below contains claims from DIFFERENT papers that a lexical detector
flagged as possibly disagreeing. Your job is to separate real disagreement from
the far more common look-alikes.

Classify each group:
  - genuine-empirical : the papers measure the same construct in comparable
                        populations and report opposite directions of effect
  - method-mismatch   : different measures, populations, timeframes, or
                        operationalisations — they are not answering one question
  - scope-mismatch    : compatible findings at different levels or boundary
                        conditions; both can be true at once
  - not-a-conflict    : the detector misfired; these claims do not oppose

Default to the mismatch categories. Two papers reporting different numbers is
almost never a genuine empirical conflict — it usually means they measured
different things. Only use genuine-empirical when the constructs really line up,
and say what would settle it.

`explanation` is under 40 words and names the specific difference you found
(which measure, which population, which timeframe). `confidence` is low when the
extracted sentences do not carry enough context to tell — that is a common and
acceptable answer here."""

CONTRADICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string",
                                "enum": ["genuine-empirical", "method-mismatch",
                                         "scope-mismatch", "not-a-conflict"]},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                    "explanation": {"type": "string"},
                    "what_would_settle_it": {"type": "string"},
                },
                "required": ["index", "verdict", "confidence", "explanation",
                             "what_would_settle_it"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def adjudicate_contradictions(candidates: list[dict], topic: str) -> list[dict] | None:
    """Triage lexical contradiction candidates. Returns the candidates with a
    verdict attached and the non-conflicts dropped, or None if unavailable."""
    ok, _ = available()
    if not ok or not candidates:
        return None

    kept = []
    for start in range(0, len(candidates), CLUSTERS_PER_CALL):
        batch = candidates[start:start + CLUSTERS_PER_CALL]
        blocks = []
        for i, c in enumerate(batch):
            blocks.append(
                "GROUP %d (topic term: %s)\n"
                "Positive direction:\n%s\n"
                "  sources: %s\n"
                "Negative/null direction:\n%s\n"
                "  sources: %s"
                % (i, c["topic"],
                   "\n".join("  - " + s for s in c["claims_for"]),
                   "; ".join(c["sources_for"]),
                   "\n".join("  - " + s for s in c["claims_against"]),
                   "; ".join(c["sources_against"])))
        user = "Review topic: %s\n\n%s" % (topic, "\n\n".join(blocks))
        try:
            # Higher effort: this is the judgment call the whole method rests on.
            data = _call(CONTRADICTION_SYSTEM, user, CONTRADICTION_SCHEMA,
                         effort="high",
                         required=("index", "verdict", "confidence", "explanation"),
                         task="contradictions")
        except AdjudicationUnavailable as e:
            print("    [adjudicate] contradiction batch failed, keeping lexical "
                  "candidates: %s" % e)
            return None
        for r in data.get("results", []):
            i = r.get("index")
            if not isinstance(i, int) or not 0 <= i < len(batch):
                continue
            if r["verdict"] not in VERDICTS or r["confidence"] not in CONFIDENCES:
                continue  # unenforced schema: ignore an out-of-vocabulary verdict
            if r["verdict"] == "not-a-conflict":
                continue
            item = dict(batch[i])
            item["type"] = r["verdict"]
            item["status"] = "adjudicated (%s confidence)" % r["confidence"]
            item["resolution"] = str(r["explanation"])
            item["what_would_settle_it"] = str(r.get("what_would_settle_it", "unstated"))
            item["verification"] = "llm-adjudicated"
            kept.append(item)
    return kept


def selftest() -> int:
    """`--check`: one cheap real call, so wiring problems surface before a run."""
    ok, reason = available()
    if not ok:
        # Nothing below is safe to resolve — models() and _openai_base_url() both
        # go through provider(), which is exactly what raises on a bad value.
        print("provider : n/a")
        print("status   : %s" % reason)
        return 1

    print("provider : %s" % provider())
    # Print the whole chain, not just the head: the fallbacks are the difference
    # between a dead model id degrading the run and a dead model id being skipped.
    default_chain = models()
    print("model    : %s" % " -> ".join(default_chain))
    for task in sorted(_TASK_ENV):
        chain = models(task)
        if chain != default_chain:
            print("  %-14s %s" % (task + ":", " -> ".join(chain)))
    if provider() == "openai":
        print("base_url : %s" % _openai_base_url())
    print("status   : %s" % reason)

    probe = [
        {"claim": "We find that a higher first offer significantly increases the "
                  "final agreed price across all three experiments.",
         "type": "untyped", "type_confidence": "low",
         "source_id": "probe", "source_doi": ""},
        {"claim": "All code and data used in this study are available at the "
                  "project repository linked in the appendix.",
         "type": "untyped", "type_confidence": "low",
         "source_id": "probe", "source_doi": ""},
    ]
    print("\nsending one 2-sentence probe...")
    out = type_claims(probe, "negotiation anchoring")
    if out is None:
        print("RESULT   : FAILED — see the message above. Adjudication would be "
              "skipped and the run would fall back to heuristics.")
        return 2

    for c in out:
        print("  %-13s substantive=%-5s %s | %s"
              % (c["type"], c.get("substantive"), c["type_confidence"],
                 c["claim"][:52] + "..."))

    good = (out[0].get("substantive") is True and out[1].get("substantive") is False)
    print("\nRESULT   : %s" % ("PASS — the model separated the empirical claim from "
                               "the data-availability boilerplate."
                               if good else
                               "REACHABLE, but the model did not classify the probe as "
                               "expected (empirical claim substantive, availability "
                               "statement not). Wiring works; judgment quality is "
                               "questionable for this task — validate before trusting."))
    print("cost log : %s" % json.dumps(R.cost_summary()))
    return 0 if good else 3


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(selftest())
    ok, reason = available()
    print(json.dumps({"available": ok, "provider": provider() if ok else None,
                      "model": model(), "reason": reason}, indent=2))
    print("\nRun `python tools/adjudicate.py --check` to make one live probe call.")
