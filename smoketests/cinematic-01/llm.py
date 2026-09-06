#!/usr/bin/env python3
"""Shared engine-direct helper for the cinematic-01 smoke lab.

Calls the llama.cpp engines straight from the client over their
OpenAI-compatible APIs (no LiteLLM gateway, no keys — the single-slot servers
ignore auth): `local-thinking` -> LFM2.5-2.6B on :8081, `local-judge` ->
granite-4.0-h-tiny on :8080. Defines the Pydantic response schemas the
generator and the test both use: StudioList, FilmList, YearAnswer.

Direct-OTEL session tracing: every call emits a client-side OpenInference
span into the Phoenix project `cdmd-lab`, tagged session.id + user.id so a
run groups into one Session in the UI. Tracing is best-effort, never raises.
"""

import atexit
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import openai
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel

MODEL = "local-judge"
DEFAULT_MAX_TOKENS = 256
TIMEOUT_S = 120
USER_ID = "edu-harness"
PHOENIX_URL = "http://localhost:6006"
PHOENIX_PROJECT = "cdmd-lab"
THINKING_URL = "http://localhost:8081"  # LFM2.5-2.6B Q4_K_M (tested model)
JUDGE_URL = "http://localhost:8080"  # granite-4.0-h-tiny Q4_K_XL (judge)
ENGINES = {
    "local-thinking": f"{THINKING_URL}/v1",
    "local-judge": f"{JUDGE_URL}/v1",
}
# engines take no auth; the SDK just demands a non-empty string
_ENGINE_KEY = "unused"

TRACING = {"enabled": True}
SESSION_STATE = {"session_id": None}
_TRACER = None
_TRACER_LOCK = threading.Lock()
_ENGINE_CLIENTS = {}
_MODEL_IDS = {}


def tracer():
    """Lazy direct-OTEL tracer into Phoenix project `cdmd-lab`; None if off.

    Returns None forever once init failed, so calls stay quiet and the smoke
    test never depends on Phoenix being up. Lock-guarded: worker threads hit
    this concurrently on the first calls.
    """
    global _TRACER
    if not TRACING["enabled"] or _TRACER is False:
        return None
    if _TRACER is None:
        with _TRACER_LOCK:
            if _TRACER is None:
                try:
                    import phoenix.otel

                    phoenix.otel.register(
                        endpoint=f"{PHOENIX_URL}/v1/traces",
                        protocol="http/protobuf",
                        project_name=PHOENIX_PROJECT,
                        batch=True,
                    )
                    _TRACER = trace.get_tracer("cinematic-01")
                    atexit.register(flush)
                except Exception as exc:  # noqa: BLE001 - tracing is best-effort
                    print(
                        f"warning: phoenix tracing disabled: "
                        f"{type(exc).__name__}: {str(exc)[:160]}",
                        file=sys.stderr,
                    )
                    _TRACER = False
    return _TRACER


def flush(timeout_s=5):
    """Force-flush the tracer provider; safe when tracing is off."""
    try:
        tp = trace.get_tracer_provider()
        if hasattr(tp, "force_flush"):
            tp.force_flush(timeout_s)
    except Exception as exc:  # noqa: BLE001 - flush is best-effort
        print(f"warning: trace flush skipped: {type(exc).__name__}: {exc}", file=sys.stderr)


@contextmanager
def session(session_id):
    """Group every chat() call until exit into one Phoenix Session.

    Plain module state, not contextvars: worker threads of the smoke test's
    ThreadPoolExecutor start with a fresh context, so ambient propagation
    would silently drop session.id on concurrent calls.
    """
    SESSION_STATE["session_id"] = str(session_id)
    try:
        yield
    finally:
        SESSION_STATE["session_id"] = None


@contextmanager
def turn(name, input_value):
    """AGENT root span for one test turn — the probe-style trace shape.

    chat() calls made inside this context nest as LLM children (same thread),
    so each test reads agent -> LLM in the UI and the Sessions row's
    first-input/last-output resolve from AGENT roots.
    """
    tr = tracer()
    if tr is None:
        yield None
        return
    with tr.start_as_current_span(name or "llm.turn") as sp:
        sp.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, "AGENT")
        sp.set_attribute(SpanAttributes.INPUT_VALUE, input_value)
        if SESSION_STATE["session_id"]:
            sp.set_attribute(
                SpanAttributes.SESSION_ID, SESSION_STATE["session_id"]
            )
        sp.set_attribute(SpanAttributes.USER_ID, USER_ID)
        yield sp


@contextmanager
def _chat_span(name, content):
    """One client-side OpenInference LLM span; yields the span or None."""
    tr = tracer()
    if tr is None:
        yield None
        return
    with tr.start_as_current_span(name) as sp:
        sp.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, "LLM")
        sp.set_attribute(SpanAttributes.INPUT_VALUE, content)
        if SESSION_STATE["session_id"]:
            sp.set_attribute(
                SpanAttributes.SESSION_ID, SESSION_STATE["session_id"]
            )
        sp.set_attribute(SpanAttributes.USER_ID, USER_ID)
        yield sp


def _stamp_output(sp, resp):
    """Output.value + token counts onto a chat span; no-op when tracing off."""
    if sp is None or resp is None:
        if sp is not None:
            sp.set_attribute(SpanAttributes.OUTPUT_VALUE, "")
        return
    sp.set_attribute(SpanAttributes.OUTPUT_VALUE, completion_text(resp))
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    for key, attr in (
        ("prompt_tokens", SpanAttributes.LLM_TOKEN_COUNT_PROMPT),
        ("completion_tokens", SpanAttributes.LLM_TOKEN_COUNT_COMPLETION),
        ("total_tokens", SpanAttributes.LLM_TOKEN_COUNT_TOTAL),
    ):
        val = getattr(usage, key, None)
        if val is not None:
            sp.set_attribute(attr, val)


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


def engine_base(alias):
    """OpenAI-compatible base URL for a lab model alias."""
    if alias not in ENGINES:
        raise ValueError(f"unknown model alias {alias!r}; known: {sorted(ENGINES)}")
    return ENGINES[alias]


def model_id(alias):
    """Served model id of an engine (llama.cpp reports the loaded GGUF)."""
    if alias not in _MODEL_IDS:
        req = urllib.request.Request(f"{engine_base(alias)}/models")
        with urllib.request.urlopen(req, timeout=10) as r:
            _MODEL_IDS[alias] = json.loads(r.read())["data"][0]["id"]
    return _MODEL_IDS[alias]


def _engine_client(alias):
    """Cached OpenAI client per engine base URL."""
    base = engine_base(alias)
    if base not in _ENGINE_CLIENTS:
        _ENGINE_CLIENTS[base] = openai.OpenAI(
            base_url=base, api_key=_ENGINE_KEY, timeout=TIMEOUT_S
        )
    return _ENGINE_CLIENTS[base]


def health(url):
    """Probe an endpoint; True on HTTP 200."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _response_format_kwarg(schema, guided):
    """Pydantic model -> llama.cpp json_schema constraint ({} when unguided)."""
    if schema is None or not guided:
        return {}
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
            },
        }
    }


def chat(
    content,
    *,
    max_tokens=DEFAULT_MAX_TOKENS,
    response_format=None,
    reasoning=None,
    retries=3,
    guided=True,
    run_name: str | None = None,
    **kv,
):
    """One engine-direct completion; returns (resp, seconds).

    `model` (kwarg) is a lab alias resolved to the engine base URL + served
    model id. Reasoning is template-driven (LFM2.5 always thinks), so the
    `reasoning` kwarg is accepted for call compatibility and not sent.
    `response_format` becomes a llama.cpp json_schema constraint unless
    guided=False; parse-then-retry stays the callers' job (query_schema /
    ask_json). Retries back off on API errors and — under a schema constraint
    — on empty completions; resp is None once retries are spent.
    """
    model = kv.pop("model", None) or MODEL
    del reasoning  # engines reason via chat template; nothing to send
    messages = [{"role": "user", "content": content}]
    schema_kwargs = _response_format_kwarg(response_format, guided)
    span_name = run_name or f"llm.chat {model}"
    last_err = "unknown"
    with _chat_span(span_name, content) as sp:
        t0 = time.perf_counter()
        for attempt in range(1, retries + 1):
            if attempt > 1:
                time.sleep(0.5 * attempt)
                messages = [
                    {
                        "role": "user",
                        "content": "Please answer again: " + content,
                    }
                ]
            try:
                resp = _engine_client(model).chat.completions.create(
                    model=model_id(model),
                    messages=messages,
                    max_tokens=max_tokens,
                    **schema_kwargs,
                    **kv,
                )
                if completion_text(resp) or not schema_kwargs:
                    _stamp_output(sp, resp)
                    return resp, time.perf_counter() - t0
                last_err = "empty completion under schema constraint"
            except openai.APIError as exc:
                last_err = f"{type(exc).__name__}: {str(exc)[:120]}"
        _stamp_output(sp, None)
        print(
            f"chat: giving up after {retries} attempts ({last_err}); "
            f"prompt head {content[:80]!r}",
            file=sys.stderr,
        )
        return None, time.perf_counter() - t0


def echo_verdict(run_name, status, *, model=None):
    """Fire a tiny verdict-echo call so the status lands in Phoenix as a named
    trace (`<run_name> PASS|FAIL`). The client-side AGENT turn root carries
    the echoed verdict as output.value so the Sessions row keeps a filled
    last output (tiny budgets often return empty model text).

    Best-effort: any failure prints a warning and returns None so status
    stamping never breaks the smoke test.
    """
    name = f"{run_name} {status}"
    prompt = f"Echo back exactly: TEST {status}"
    try:
        with turn(name, prompt) as sp:
            resp, _ = chat(
                prompt,
                max_tokens=8,
                reasoning={"enabled": False},
                retries=1,
                run_name=name,
                model=model,
            )
            text = completion_text(resp)
            if sp is not None:
                sp.set_attribute(
                    SpanAttributes.OUTPUT_VALUE, text or f"TEST {status}"
                )
    except Exception as exc:  # noqa: BLE001 - status stamping is best-effort
        print(
            f"warning: echo_verdict({name!r}) skipped: "
            f"{type(exc).__name__}: {str(exc)[:160]}",
            file=sys.stderr,
        )
        return None
    return text


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

