#!/usr/bin/env python3
"""Shared LiteLLM helper for the cinematic-01 smoke lab.

Resolves the gateway master key at runtime (env var, then docker/.env, else a
demo placeholder that is never the real key), wraps the verified
`litellm.completion` call against the `local-judge` alias, and defines the
Pydantic response schemas the generator and the test both use: StudioList,
FilmList, YearAnswer.

Direct-OTEL session tracing: every chat() call also emits a client-side
OpenInference LLM span (input.value/output.value/llm.token_count.*) into the
Phoenix project `cdmd-lab`, tagged session.id + user.id so a run groups into
one Session in the UI. The gateway metering path (project `default`) stays
untouched; tracing is best-effort and never raises.
"""

import atexit
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import litellm
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel

MODEL = "local-judge"
DEFAULT_BASE_URL = "http://localhost:4000"
DEFAULT_MAX_TOKENS = 256
DEFAULT_REASONING = {"enabled": True}
DEMO_KEY = "sk-1234-master-key-4321"
TIMEOUT_S = 120
USER_ID = "edu-harness"
PHOENIX_URL = "http://localhost:6006"
PHOENIX_PROJECT = "cdmd-lab"

TRACING = {"enabled": True}
SESSION_STATE = {"session_id": None}
_TRACER = None
_TRACER_LOCK = threading.Lock()


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
    span_name = run_name or f"llm.chat {model}"
    with _chat_span(span_name, content) as sp:
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
                _stamp_output(sp, resp)
                return resp, time.perf_counter() - t0
            except litellm.exceptions.JSONSchemaValidationError:
                if attempt == retries:
                    print(
                        "chat: validation still failing, last attempt",
                        repr(content)[:160],
                        file=sys.stderr,
                    )
                    _stamp_output(sp, None)
                    return None, time.perf_counter() - t0
        raise RuntimeError("unreachable")


def echo_verdict(run_name, status, *, base_url=DEFAULT_BASE_URL, model=None):
    """Fire a tiny verdict-echo call so the status lands in Phoenix as a named
    trace (`<run_name> PASS|FAIL`); the gateway stamper derives the filterable
    `metadata.test_status` attribute from the name token. The client-side
    AGENT turn root carries the echoed verdict as output.value so the
    Sessions row keeps a filled last output (tiny budgets often return empty
    model text).

    Best-effort: any failure prints a warning and returns None so status
    stamping never breaks the smoke test.
    """
    name = f"{run_name} {status}"
    prompt = f"Echo back exactly: TEST {status}"
    try:
        with turn(name, prompt) as sp:
            resp, _ = chat(
                prompt,
                base_url=base_url,
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

