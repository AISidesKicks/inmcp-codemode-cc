#!/usr/bin/env python3
"""Shared LiteLLM helper for the cinematic-01 smoke lab.

Resolves the gateway master key at runtime (env var, then docker/.env, else a
demo placeholder that is never the real key), wraps the verified
`litellm.completion` call against the `local-gguf` alias, and defines the
Pydantic response schemas the generator and the test both use: StudioList,
FilmList, YearAnswer.
"""

import os
import sys
import time
import urllib.error
import urllib.request

import litellm
from pydantic import BaseModel

MODEL = "local-gguf"
DEFAULT_BASE_URL = "http://localhost:4000"
DEFAULT_MAX_TOKENS = 256
DEFAULT_REASONING = {"enabled": False}
CACHE_PARAM = {}  # LiteLLM Redis caching; boolean True regresses with 400s

# Cache-mode presets passed per call as the litellm `cache` kwarg.
#   1level  - bypass Redis reads entirely; the engine prefix-cache tier does the
#             reuse (distinct +1/+2/+3 suffixes share a common prefix).
#   2level  - legacy two-level wiring: LiteLLM Redis reuses identical prompts.
#   no-cache- same per-request skip as 1level but with fully cold prompts so
#             no tier (Redis or engine prefix) can reuse anything.
CACHE_MODES = {
    "1level": {"no-cache": True},
    "2level": {},
    "no-cache": {"no-cache": True},
}
DEMO_KEY = "sk-1234-master-key-4321"
TIMEOUT_S = 120


class StudioList(BaseModel):
    """Answer naming one or more studios."""

    studios: list[str]


class FilmList(BaseModel):
    """Answer naming the films a studio is credited with."""

    films: list[str]


class YearAnswer(BaseModel):
    """Answer naming a single film's release year."""

    title: str
    year: int


def get_repo_root():
    """Repo root: three levels up from smoketests/cinematic-01/llm.py."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_master_key():
    """Runtime master key: env var, then docker/.env, then a demo placeholder.

    The real key never lands in source; the placeholder is used only so failing
    calls fail loudly against the gateway instead of crash on an empty key.
    """
    key = os.environ.get("LITELLM_MASTER_KEY")
    if key:
        return key.strip().strip("\"'")
    env_file = os.path.join(get_repo_root(), "docker", ".env")
    try:
        with open(env_file, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("LITELLM_MASTER_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    print("WARNING: LITELLM_MASTER_KEY not found, using demo default", file=sys.stderr)
    return DEMO_KEY


def health(url):
    """Probe an endpoint; True on HTTP 200."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status == 200
    except (OSError, urllib.error.URLError):
        return False


def chat(
    content,
    *,
    base_url=DEFAULT_BASE_URL,
    max_tokens=DEFAULT_MAX_TOKENS,
    response_format=None,
    reasoning=None,
    retries=3,
    guided=True,
    **kv,
):
    """Cache-enabled completion via the LiteLLM SDK; returns (resp, seconds).

    On schema validation errors (empty/truncated completions), re-asks with a
    prefixed prompt and a higher generation budget until retries are spent,
    backing off between attempts. `guided` toggles the LiteLLM JSON-schema
    validation path (`enable_json_schema_validation`); some engines (vLLM
    W8A16) return empty completions under guided decoding, so callers may pass
    `guided=False` and rely on schema-parse-then-retry instead.
    """
    cache_mode = kv.pop("cache_mode", None)
    model = kv.pop("model", None) or MODEL
    cache_param = CACHE_MODES.get(
        str(cache_mode) if cache_mode is not None else "", CACHE_PARAM
    )
    kwargs = {
        "model": model,
        "base_url": base_url,
        "custom_llm_provider": "openai",
        "api_key": get_master_key(),
        "cache": dict(cache_param),
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    }
    if reasoning is not None:
        kwargs["reasoning"] = reasoning
    if response_format is not None and guided:
        kwargs["response_format"] = response_format
        kwargs["enable_json_schema_validation"] = True
    kwargs.update(kv)
    t0 = time.perf_counter()
    for attempt in range(1, retries + 1):
        if attempt > 1:
            time.sleep(0.5 * attempt)
            kwargs["messages"] = [
                {
                    "role": "user",
                    "content": "Please answer again: " + content,
                }
            ]
            kwargs["max_tokens"] = max(1024, max_tokens)
        try:
            resp = litellm.completion(**kwargs)
            return resp, time.perf_counter() - t0
        except litellm.exceptions.JSONSchemaValidationError:
            if attempt == retries:
                print(
                    "chat: validation still failing, last attempt",
                    repr(content)[:160],
                    file=sys.stderr,
                )
                return None, time.perf_counter() - t0
    raise RuntimeError("unreachable")


def completion_text(resp):
    """Primary answer text from a completion response."""
    try:
        return (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        return ""


def reasoning_content(resp, limit=400):
    """Thinking snippet from a completion response, else None.

    Tries the first-class `message.reasoning_content` field, then common
    provider extras (reasoning_content/reasoning/thinking) under
    `model_extra`, so llama.cpp/vLLM/SGLang response shapes all resolve.
    Long reasoning is trimmed with an overflow marker.
    """
    message = getattr(getattr(resp, "choices", [None])[0], "message", None)
    if message is None:
        return None
    text = getattr(message, "reasoning_content", None)
    if not isinstance(text, str) or not text:
        extra = getattr(message, "model_extra", None) or {}
        text = None
        for key in ("reasoning_content", "reasoning", "thinking"):
            candidate = extra.get(key)
            if isinstance(candidate, str) and candidate:
                text = candidate
                break
    if not isinstance(text, str) or not text:
        return None
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def cache_regime(resp):
    """litellm-redis-hit iff hidden params carry the x-litellm-cache-key header."""
    hidden = getattr(resp, "_hidden_params", None) or {}
    headers = hidden.get("additional_headers") or {}
    items = headers.items() if isinstance(headers, dict) else headers
    for key, _ in items:
        if "x-litellm-cache-key" in str(key).lower():
            return "litellm-redis-hit"
    return "litellm-redis-miss"


def usage_fields(resp):
    """Token counts from the response usage block."""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def cached_tokens(resp):
    """Engine-prefix-reuse tokens from the response, or None.

    vLLM reports `usage.prompt_tokens_details.cached_tokens` first-class; the
    no-cache/1level routes never hit LiteLLM Redis, so this is the only signal
    that the engine's prefix cache actually replayed tokens. Falls back to the
    same field under `model_extra` for providers that stuff it there.
    """
    usage = getattr(resp, "usage", None)
    details = (
        getattr(usage, "prompt_tokens_details", None) if usage is not None else None
    )
    value = None
    if details is not None:
        value = getattr(details, "cached_tokens", None)
    if value is None:
        extra = getattr(usage, "model_extra", None) if usage is not None else None
        details = (
            (extra or {}).get("prompt_tokens_details")
            if isinstance(extra, dict)
            else None
        )
        if isinstance(details, dict):
            value = details.get("cached_tokens")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def vllm_prefix_metrics(base_url="http://localhost:8000"):
    """Snapshot of vLLM's kernel prefix-cache counters, or None if unreachable.

    vLLM 0.27 serves prompt reuse only through `/metrics`
    (`vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total`); the
    per-request `usage.prompt_tokens_details.cached_tokens` field is absent in
    this build. Returns {"hits": int, "queries": int} so a demo can delta two
    snapshots and prove the engine's prefix tier replayed tokens.
    """
    try:
        with urllib.request.urlopen(base_url + "/metrics", timeout=10) as resp:
            text = resp.read().decode("utf-8", "replace")
    except (OSError, urllib.error.URLError):
        return None
    counts = {}
    for line in text.splitlines():
        if line.startswith("vllm:prefix_cache_hits_total{"):
            counts["hits"] = int(float(line.rsplit(" ", 1)[-1]))
        elif line.startswith("vllm:prefix_cache_queries_total{"):
            counts["queries"] = int(float(line.rsplit(" ", 1)[-1]))
    return counts if {"hits", "queries"} <= counts.keys() else None


def sglang_prefix_metrics(base_url="http://localhost:30000"):
    """Snapshot of SGLang's KV-prefix-cache counters, or None if unreachable.

    SGLang serves token reuse through `/metrics` Prometheus gauges: a cache hit
    rate (`sglang:cache_hit_rate`) and the KV cache memory resident in GB
    (`sglang:kv_cache_memory_usage_gb`). Unlike vLLM's absolute hit/query
    counters, these are rates/gauge snapshots, so a demo records them at the
    wall-clock moment of the probe rather than as a cumulative delta. Returns
    {"hits": float, "queries": float} (as the hit rate and KV-cache GB) so the
    cache-demo row stays the same shape.
    """
    try:
        with urllib.request.urlopen(base_url + "/metrics", timeout=10) as resp:
            text = resp.read().decode("utf-8", "replace")
    except (OSError, urllib.error.URLError):
        return None
    counts = {}
    for line in text.splitlines():
        if line.startswith("sglang:cache_hit_rate{"):
            counts["hits"] = float(line.rsplit(" ", 1)[-1])
        elif line.startswith("sglang:kv_cache_memory_usage_gb{"):
            counts["queries"] = float(line.rsplit(" ", 1)[-1])
    return counts if {"hits", "queries"} <= counts.keys() else None


def timings(resp):
    """llama.cpp prompt/cache timings; live in model_extra, not usage, here."""
    extra = getattr(resp, "model_extra", None) or {}
    if not isinstance(extra, dict):
        return {}
    t = extra.get("timings") or {}
    if not isinstance(t, dict):
        return {}
    return {
        "prompt_n": t.get("prompt_n"),
        "cache_n": t.get("cache_n"),
        "predicted_n": t.get("predicted_n"),
    }
