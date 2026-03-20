"""Manual OpenTelemetry instrumentation helpers for GenAI agents.

Implements:
  - invoke_agent span + duration/token metrics  (gen-ai-agent-spans.md)
  - execute_tool span                           (gen-ai-spans.md)
  - gen_ai.client.operation.duration metric     (gen-ai-metrics.md)
  - gen_ai.client.token.usage metric            (gen-ai-metrics.md)
"""

import dataclasses
import json
import os
import time
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace, _logs
from opentelemetry._logs import SeverityNumber
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, SpanKind, StatusCode, TraceFlags
from opentelemetry.util.genai.completion_hook import load_completion_hook
from opentelemetry.util.genai.types import InputMessage, OutputMessage, Text
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

_CAPTURE_MESSAGE_CONTENT_ENVVAR = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"


_capture_on_span: bool = os.environ.get(_CAPTURE_MESSAGE_CONTENT_ENVVAR, "").lower() == "span_only"

_tracer = trace.get_tracer("halluchr", "0.1.0")
_meter = metrics.get_meter("halluchr", "0.1.0")
_logger = _logs.get_logger("halluchr", "0.1.0")
_completion_hook = load_completion_hook()

_operation_duration = _meter.create_histogram(
    "gen_ai.client.operation.duration",
    unit="s",
    description="Duration of GenAI client operations.",
)

_token_usage = _meter.create_histogram(
    "gen_ai.client.token.usage",
    unit="{token}",
    description="Number of tokens used in GenAI operations.",
)


def _build_input_messages(message_history, prompt: str) -> list[InputMessage]:
    """Convert pydantic-ai message history + current prompt to InputMessage list."""
    inputs: list[InputMessage] = []
    for msg in (message_history or []):
        if isinstance(msg, ModelResponse):
            texts = [Text(p.content) for p in msg.parts if isinstance(p, TextPart)]
            if texts:
                inputs.append(InputMessage(role="assistant", parts=texts))
        elif isinstance(msg, ModelRequest):
            texts = [Text(p.content) for p in msg.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str)]
            if texts:
                inputs.append(InputMessage(role="user", parts=texts))
    inputs.append(InputMessage(role="user", parts=[Text(prompt)]))
    return inputs


def _parse_model_string(model_str: str) -> tuple[str, str]:
    """Split 'provider:model_name' into (provider, model_name)."""
    if ":" in model_str:
        provider, model = model_str.split(":", 1)
        return provider, model
    return "openai", model_str


async def run_agent(
    agent,
    prompt: str,
    *,
    agent_name: str,
    model_str: str,
    system_prompt: str | None = None,
    deps=None,
    message_history=None,
):
    """Run a pydantic-ai agent wrapped in an invoke_agent span with metrics.

    Args:
        agent:        The pydantic-ai Agent instance.
        prompt:       The user-facing prompt for this run.
        agent_name:   Human-readable agent name (gen_ai.agent.name).
        model_str:    Model string as passed to Agent, e.g. "openai:gpt-4o".
        system_prompt: The agent's system prompt text (opt-in attribute).
        deps:         Optional deps object forwarded to agent.run().

    Returns:
        The raw pydantic-ai RunResult so callers can access .output and .usage().
    """
    provider, model = _parse_model_string(model_str)

    span_attrs: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "gen_ai.agent.name": agent_name,
    }

    metric_attrs: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
    }

    start = time.monotonic()

    with _tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        kind=SpanKind.INTERNAL,
        attributes=span_attrs,
    ) as span:
        if span.is_recording() or _capture_on_span:
            sys_parts = [Text(system_prompt)] if system_prompt else []
            input_messages = _build_input_messages(message_history, prompt)
        else:
            sys_parts = []
            input_messages = []

        if span.is_recording() and _capture_on_span:
            if sys_parts:
                span.set_attribute(
                    "gen_ai.system_instructions",
                    json.dumps([dataclasses.asdict(p) for p in sys_parts]),
                )
            span.set_attribute(
                "gen_ai.input.messages",
                json.dumps([dataclasses.asdict(m) for m in input_messages]),
            )

        try:
            kwargs = {}
            if deps is not None:
                kwargs["deps"] = deps
            if message_history is not None:
                kwargs["message_history"] = message_history
            result = await agent.run(prompt, **kwargs)

            # Record usage on the span
            usage = result.usage()
            if usage.input_tokens:
                span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
                _token_usage.record(usage.input_tokens, {**metric_attrs, "gen_ai.token.type": "input"})
            if usage.output_tokens:
                span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
                _token_usage.record(usage.output_tokens, {**metric_attrs, "gen_ai.token.type": "output"})
            if usage.cache_write_tokens:
                span.set_attribute("gen_ai.usage.cache_creation.input_tokens", usage.cache_write_tokens)
            if usage.cache_read_tokens:
                span.set_attribute("gen_ai.usage.cache_read.input_tokens", usage.cache_read_tokens)

            if span.is_recording():
                output = result.output
                output_content = (
                    json.dumps(output.model_dump()) if hasattr(output, "model_dump") else str(output)
                )
                output_messages = [OutputMessage(role="assistant", parts=[Text(output_content)], finish_reason="stop")]

                if _capture_on_span:
                    span.set_attribute(
                        "gen_ai.output.messages",
                        json.dumps([dataclasses.asdict(m) for m in output_messages]),
                    )

                _completion_hook.on_completion(
                    inputs=input_messages,
                    outputs=output_messages,
                    system_instruction=sys_parts,
                    span=None if _capture_on_span else span,
                )

            _operation_duration.record(time.monotonic() - start, metric_attrs)
            return result

        except Exception as exc:
            error_type = type(exc).__qualname__
            span.set_status(StatusCode.ERROR, str(exc))
            span.set_attribute("error.type", error_type)
            _operation_duration.record(
                time.monotonic() - start,
                {**metric_attrs, "error.type": error_type},
            )
            raise


def emit_evaluation_event(
    rating: int,
    comment: str | None,
    trace_id_hex: str | None,
    span_id_hex: str | None,
) -> None:
    """Emit a gen_ai.evaluation.result event linked to the original inquiry span.

    Args:
        rating:       1–5 star rating from the user.
        comment:      Optional free-text comment.
        trace_id_hex: 32-char hex trace-id from the inquiry SSE done event.
        span_id_hex:  16-char hex span-id from the inquiry SSE done event.
    """
    attrs: dict[str, Any] = {
        "event.name": "gen_ai.evaluation.result",
        "gen_ai.evaluation.name": "user_feedback",
        "gen_ai.evaluation.score.value": float(rating)
    }
    if comment:
        attrs["gen_ai.evaluation.explanation"] = comment

    ctx = None
    if trace_id_hex and span_id_hex:
        span_ctx = SpanContext(
            trace_id=int(trace_id_hex, 16),
            span_id=int(span_id_hex, 16),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        ctx = trace.set_span_in_context(NonRecordingSpan(span_ctx))

    _logger.emit(
        event_name="gen_ai.evaluation.result",
        context=ctx,
        severity_number=SeverityNumber.INFO,
        attributes=attrs,
    )


class WorkflowContext:
    """Yielded by :func:`workflow_span` so callers can record the final output."""

    def __init__(self, span: Span) -> None:
        self.span = span
        self.output: str | None = None


@contextmanager
def workflow_span(workflow_name: str, *, user_input: str, user_id: str | None = None):
    """Context manager that wraps a multi-agent workflow in an invoke_workflow span.

    Args:
        workflow_name: Human-readable name for the workflow (gen_ai.workflow.name).
        user_input:    The original user message (recorded as input message).

    Yields a :class:`WorkflowContext`.  Set ``ctx.output`` before exiting the
    block so the completion hook (and optional span attribute) receive the
    final response text.
    """
    span_attrs: dict[str, Any] = {
        "gen_ai.operation.name": "invoke_workflow",
        "gen_ai.workflow.name": workflow_name,
    }
    if user_id:
        span_attrs["enduser.pseudo.id"] = user_id

    with _tracer.start_as_current_span(
        f"invoke_workflow {workflow_name}",
        kind=SpanKind.INTERNAL,
        attributes=span_attrs,
    ) as span:
        if span.is_recording() and _capture_on_span:
            input_messages = [InputMessage(role="user", parts=[Text(user_input)])]
            span.set_attribute(
                "gen_ai.input.messages",
                json.dumps([dataclasses.asdict(m) for m in input_messages]),
            )

        ctx = WorkflowContext(span)
        try:
            yield ctx
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.set_attribute("error.type", type(exc).__qualname__)
            raise
        else:
            if span.is_recording() and ctx.output is not None:
                output_messages = [OutputMessage(role="assistant", parts=[Text(ctx.output)], finish_reason="stop")]
                if _capture_on_span:
                    span.set_attribute(
                        "gen_ai.output.messages",
                        json.dumps([dataclasses.asdict(m) for m in output_messages]),
                    )
                _completion_hook.on_completion(
                    inputs=[InputMessage(role="user", parts=[Text(user_input)])],
                    outputs=output_messages,
                    system_instruction=[],
                    span=None if _capture_on_span else span,
                )


@contextmanager
def tool_span(tool_name: str, *, call_id: str | None = None, arguments: dict | None = None):
    """Context manager that wraps a tool call in an execute_tool span.

    Args:
        tool_name:  Name of the tool (gen_ai.tool.name).
        call_id:    Tool call ID from the model (gen_ai.tool.call.id).
        arguments:  Tool arguments dict (opt-in, recorded as JSON).

    Yields the active span so callers can set gen_ai.tool.call.result.
    """
    span_attrs: dict[str, Any] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": tool_name,
    }
    if call_id:
        span_attrs["gen_ai.tool.call.id"] = call_id

    with _tracer.start_as_current_span(
        f"execute_tool {tool_name}",
        kind=SpanKind.INTERNAL,
        attributes=span_attrs,
    ) as span:
        if arguments is not None:
            span.set_attribute("gen_ai.tool.call.arguments", json.dumps(arguments))
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.set_attribute("error.type", type(exc).__qualname__)
            raise
