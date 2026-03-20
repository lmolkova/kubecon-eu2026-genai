"""OpenTelemetry setup - traces, metrics, and logs with OTLP gRPC export."""

import logging
import sys

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, LogRecordProcessor
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.metrics._internal.aggregation import ExplicitBucketHistogramAggregation
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor


_LOG_RECORD_BUILTIN_ATTRS = {
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs", "msg",
    "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "taskName", "thread", "threadName",
}


class ExtraAttrsFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _LOG_RECORD_BUILTIN_ATTRS}
        if extras:
            attrs_str = " ".join(f"{k}={v!r}" for k, v in extras.items())
            msg = f"{msg} {attrs_str}"
        return msg


class FilteringSpanExporter(SpanExporter):
    _SUFFIXES = ("http receive", "http send")

    def __init__(self, exporter: SpanExporter):
        self._exporter = exporter

    def export(self, spans):
        filtered = [s for s in spans if not any(s.name.lower().endswith(suffix) for suffix in self._SUFFIXES)]
        if not filtered:
            return SpanExportResult.SUCCESS
        return self._exporter.export(filtered)

    def shutdown(self):
        self._exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self._exporter.force_flush(timeout_millis)


class DropCodeAttributesLogProcessor(LogRecordProcessor):
    def on_emit(self, log_record):
        attrs = log_record.log_record.attributes
        if attrs:
            log_record.log_record.attributes = {k: v for k, v in attrs.items() if not k.startswith("code")}

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


def configure_opentelemetry() -> None:
    _configure_traces()
    _configure_metrics()
    _configure_logs()
    AsyncPGInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    OpenAIInstrumentor().instrument()


def _configure_traces() -> None:
    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(FilteringSpanExporter(OTLPSpanExporter()))
    )
    trace.set_tracer_provider(provider)


def _configure_metrics() -> None:
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(),
        export_interval_millis=10_000,
    )
    # Enforce Gen AI semantic convention bucket boundaries (seconds) for
    # gen_ai.client.operation.duration across all meters/instrumentations.
    # Without this, the manual histogram and the auto-instrumented one use
    # different default boundaries, producing an invalid merged histogram.
    gen_ai_duration_view = View(
        instrument_name="gen_ai.client.operation.duration",
        aggregation=ExplicitBucketHistogramAggregation(
            boundaries=[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]
        ),
    )
    provider = MeterProvider(metric_readers=[reader], views=[gen_ai_duration_view])
    metrics.set_meter_provider(provider)


def _configure_logs() -> None:
    provider = LoggerProvider()
    provider.add_log_record_processor(DropCodeAttributesLogProcessor())
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter())
    )
    set_logger_provider(provider)

    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(ExtraAttrsFormatter())
    logging.getLogger().addHandler(stream_handler)
