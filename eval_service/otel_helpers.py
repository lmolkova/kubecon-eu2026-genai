"""OpenTelemetry setup and span utilities for the eval service."""

import base64
import logging

from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import SeverityNumber, set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

logger = logging.getLogger(__name__)

_otel_logger = _logs.get_logger("evals", "0.1.0")


def configure_otel() -> None:
    global _otel_logger

    trace_provider = TracerProvider()
    trace_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(trace_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(),
        export_interval_millis=10_000,
    )
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    log_provider = LoggerProvider()
    log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(log_provider)

    logging.basicConfig(level=logging.INFO)

    _otel_logger = _logs.get_logger("evals", "0.1.0")

    HTTPXClientInstrumentor().instrument()
    OpenAIInstrumentor().instrument()


def attr_value(v: dict):
    for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if kind in v:
            return v[kind]
    return None


def extract_spans(trace_data: dict) -> tuple[dict[str, dict], list[dict]]:
    """Return agent spans by name and all spans in the trace."""
    spans_by_agent: dict[str, dict] = {}
    all_spans: list[dict] = []
    for resource_span in trace_data.get("batches", []):
        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                attrs = {a["key"]: attr_value(a["value"]) for a in span.get("attributes", [])}
                enriched = {**span, "_attrs": attrs}
                all_spans.append(enriched)
                agent_name = attrs.get("gen_ai.agent.name")
                if agent_name:
                    spans_by_agent[agent_name] = enriched
    return spans_by_agent, all_spans


def span_id_hex(span: dict) -> str:
    return base64.b64decode(span["spanId"]).hex()


def id_to_int(value: str) -> int:
    """Parse a trace/span ID that may be hex or base64 into an integer."""
    clean = value.replace("-", "")
    try:
        return int(clean, 16)
    except ValueError:
        return int(base64.b64decode(clean).hex(), 16)


def span_subtree(root: dict, all_spans: list[dict]) -> list[dict]:
    """Return all descendants of root (not including root itself)."""
    children = []
    queue = [root["spanId"]]
    while queue:
        parent_id = queue.pop()
        for s in all_spans:
            if s.get("parentSpanId") == parent_id:
                children.append(s)
                queue.append(s["spanId"])
    return children


def span_has_error(span: dict) -> bool:
    if span["_attrs"].get("error.type"):
        return True
    status = span.get("status", {})
    code = status.get("code", "")
    return code in ("STATUS_CODE_ERROR", 2)


def emit_eval_events(trace_id_hex: str, span_id_hex_: str, verdicts: dict, reasonings: dict[str, str]) -> None:
    span_ctx = SpanContext(
        trace_id=id_to_int(trace_id_hex),
        span_id=id_to_int(span_id_hex_),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    ctx = trace.set_span_in_context(NonRecordingSpan(span_ctx))

    for metric_name, passed in verdicts.items():
        _otel_logger.emit(
            event_name="gen_ai.evaluation.result",
            context=ctx,
            severity_number=SeverityNumber.INFO,
            attributes={
                "event.name": "gen_ai.evaluation.result",
                "gen_ai.evaluation.name": metric_name,
                "gen_ai.evaluation.score.value": 1.0 if passed else 0.0,
                "gen_ai.evaluation.explanation": reasonings.get(metric_name, ""),
            },
        )
    logger.info("Evaluated trace", extra={"trace_id": trace_id_hex, **{k: "PASS" if v else "FAIL" for k, v in verdicts.items()}})
