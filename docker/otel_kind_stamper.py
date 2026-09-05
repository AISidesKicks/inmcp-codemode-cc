"""Stamp OpenInference span kinds on litellm v1 proxy spans for Phoenix.

Attaches lazily to the global TracerProvider initialized by the "otel" callback.
Root request span -> CHAIN; model-call spans (litellm_request, raw_gen_ai_request,
generation_name-named) -> LLM; internal child spans left untouched.
"""
from typing import Any

from litellm.integrations.custom_logger import CustomLogger
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

_ROOT_SPAN_NAME = "Received Proxy Server Request"
_INTERNAL_SPAN_NAMES = frozenset({"auth", "proxy_pre_call", "router", "self", _ROOT_SPAN_NAME})
_DEFAULT_SPAN_NAMES = frozenset({"litellm_request", "raw_gen_ai_request"})
_KIND_KEY = "openinference.span.kind"

_attached = False


class SpanKindStamper(CustomLogger, SpanProcessor):
    def _attach(self) -> None:
        global _attached
        if _attached:
            return
        try:
            candidates = [trace.get_tracer_provider()]
            try:
                import litellm.proxy.proxy_server as ps

                otel_logger = getattr(ps, "open_telemetry_logger", None)
                if otel_logger is not None:
                    candidates.insert(0, getattr(otel_logger, "_tracer_provider", None))
            except Exception:  # noqa: BLE001, S110 - attach is best-effort
                pass
            for provider in candidates:
                if provider is not None and hasattr(provider, "add_span_processor"):
                    provider.add_span_processor(self)
                    _attached = True
                    return
        except Exception:  # noqa: BLE001, S110 - stamping must never break startup
            pass

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        try:
            if span.name == _ROOT_SPAN_NAME and span.parent is None:
                span.set_attribute(_KIND_KEY, "CHAIN")
            elif span.name not in _INTERNAL_SPAN_NAMES:
                span.set_attribute(_KIND_KEY, "LLM")
                if span.name not in _DEFAULT_SPAN_NAMES:
                    span.set_attribute("metadata.generation_name", span.name)
                    span.set_attribute("metadata.run_id", span.name.split(" ", 1)[0])
        except Exception:  # noqa: BLE001, S110 - never break the request path
            pass

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        self._attach()
        return data


span_kind_stamper = SpanKindStamper()
span_kind_stamper._attach()
