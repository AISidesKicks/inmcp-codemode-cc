#!/usr/bin/env python3
"""Shared LiteLLM helper for the cinematic-01 smoke lab.

Resolves the gateway master key at runtime (env var, then docker/.env, else a
demo placeholder that is never the real key), wraps the verified
`litellm.completion` call against the `local-judge` alias, and defines the
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

MODEL = "local-judge"
DEFAULT_BASE_URL = "http://localhost:4000"
DEFAULT_MAX_TOKENS = 256
DEFAULT_REASONING = {"enabled": True}
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
    run_name: str | None = None,
    **kv,
):
    """Completion via the LiteLLM SDK; returns (resp, seconds).

    On schema validation errors (empty/truncated completions), re-asks with a
    prefixed prompt and a higher generation budget until retries are spent,
    backing off between attempts. `guided` toggles the LiteLLM JSON-schema
    validation path (`enable_json_schema_validation`); some engines (vLLM
    W8A16) return empty completions under guided decoding, so callers may pass
    `guided=False` and rely on schema-parse-then-retry instead.
    """
    model = kv.pop("model", None) or MODEL
    # Reasoning is always on: LFM2.5 is a pure reasoning model, the old
    # budget-0 off-switch fought the template. Callers may override the dict.
    kwargs = {
        "model": model,
        "base_url": base_url,
        "custom_llm_provider": "openai",
        "api_key": get_master_key(),
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "reasoning": reasoning or dict(DEFAULT_REASONING),
    }
    if response_format is not None and guided:
        kwargs["response_format"] = response_format
        kwargs["enable_json_schema_validation"] = True
    kwargs.update(kv)
    if run_name:
        metadata = kwargs.pop("metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.setdefault("generation_name", str(run_name))
        extra_body = kwargs.pop("extra_body", None)
        if not isinstance(extra_body, dict):
            extra_body = {}
        extra_body.setdefault("metadata", metadata)
        kwargs["extra_body"] = extra_body
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

