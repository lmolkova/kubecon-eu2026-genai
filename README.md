# HallucHR

A demo HR AI assistant built with [pydantic-ai](https://ai.pydantic.dev/), FastAPI, and PostgreSQL, with full OpenTelemetry observability (traces, metrics, logs) exported to a local Grafana LGTM stack.

## Architecture

```text
Employee (browser)
   │  POST /inquiry (multipart/form-data)
   ▼
FastAPI backend
   │
   ├── 1. Intake Agent        - classify, assess severity, route
   │
   ├── [direct escalation path if intake.route_to_escalation=true]
   │
   ├── 2. Advisor Agent        - look up HR data + policies, answer
   │        ▲  │ revision feedback (up to 2 rounds)
   │        │  ▼
   └── 3. Review Agent        - approve / request revision / escalate
            │
            ▼
       PostgreSQL (employee records)
       Knowledge Base (markdown policy files)

Eval Service (background)
   │  polls Tempo for recent traces
   ▼
LLM-as-judge (gpt-4o-mini) → gen_ai.evaluation.result log events → Loki
```

Results are streamed back to the browser via Server-Sent Events (SSE).

## Observability Stack

The backend emits OpenTelemetry traces, metrics, and logs via OTLP gRPC.
Docker Compose includes the full Grafana LGTM stack:

| Service | Port | Purpose |
| --- | --- | --- |
| Grafana | [3000](http://localhost:3000) | Dashboards, Explore (no login required) |
| Tempo | 3200 | Distributed tracing |
| Loki | 3100 | Logs (including eval results) |
| Mimir | 9009 | Metrics |
| OTel Collector | 4317/4318 | OTLP gRPC/HTTP receiver |

### Instrumentation

The backend uses a mix of auto-instrumentation and manual instrumentation following the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/).

**Auto-instrumented** (via OTel contrib packages):

- OpenAI API calls - `opentelemetry-instrumentation-openai-v2` (LLM spans with token usage)
- FastAPI HTTP server - `opentelemetry-instrumentation-fastapi`
- PostgreSQL - `opentelemetry-instrumentation-asyncpg`
- Outbound HTTP - `opentelemetry-instrumentation-httpx`

**Manually instrumented** ([spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/) · [agent spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/) · [metrics](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/)) - in [backend/agents/otel_helpers.py](backend/agents/otel_helpers.py):

| Span / metric | `gen_ai.operation.name` | Key attributes |
| --- | --- | --- |
| Workflow span | `invoke_workflow` | `gen_ai.workflow.name`, input/output messages |
| Agent span | `invoke_agent` | `gen_ai.agent.name`, token usage |
| Tool span | `execute_tool` | `gen_ai.tool.name`, arguments, result |
| Duration metric | `gen_ai.client.operation.duration` | per operation + provider |
| Token metric | `gen_ai.client.token.usage` | input / output token counts |

**Events** ([semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/)):

- `gen_ai.evaluation.result` - emitted on user feedback (1–5 stars via `/feedback`) and by the eval service (LLM-as-judge), both linked to the original `invoke_workflow` span via trace/span context

**Message content capture** is controlled by `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`:

- `span_only` - stores content as span attributes (default in docker-compose)
- unset + `OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload` - uploads to S3, stores a reference URL on the span (`gen_ai.output.messages_ref`)

## Quick Start

### Prerequisites

- Docker + Docker Compose
- An OpenAI API key

```bash
OPENAI_API_KEY=sk-... docker-compose up --build
```

The app (and the frontend) is served at **[localhost:8000](http://localhost:8000)**.
Grafana is available at **[localhost:3000](http://localhost:3000)**.

### Optional environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_MODEL` | `openai:gpt-4o` | Model used by the HR agents |
| `LLM_TEMPERATURE` | - | Override model temperature |
| `OPENAI_BASE_URL` | - | Custom OpenAI-compatible base URL |
| `OPENAI_API_KEY_EVAL` | `OPENAI_API_KEY` | Separate key for the eval service judge |

## Test Scenarios

Login with employee ID as username and `pas$word1!` as password.

| Employee | Inquiry | Expected outcome |
| --- | --- | --- |
| E001 | "How many vacation days do I have left?" | Policy agent answers with real balance; no escalation |
| E002 | "I need emergency sick leave starting tomorrow" | Urgent; escalation agent flags it |
| E003 | "My manager made inappropriate comments about my gender" | Immediate escalation, confidential flag |
| E004 | "Can I get a salary raise? I think I'm underpaid compared to peers." | Policy agent explains process; potential escalation if pay equity concern |

## Eval Service

A background service (`eval_service/`) polls Tempo for recent traces and uses `gpt-4o-mini` as an LLM-as-judge to score response quality. Evaluation results are emitted as `gen_ai.evaluation.result` log events linked to the original trace and stored in Loki.

See [eval_service/README.md](eval_service/README.md) for details on metrics, prompts, and configuration.

## LLM Evals (Unit Tests)

`tests/` contains LLM-based evals for individual agents. These call the OpenAI API and cost money - **do not run automatically**.

Requires `OPENAI_API_KEY` in a `.env` file or as an environment variable.

```bash
pytest tests/
# or specific files:
pytest tests/test_intake.py
pytest tests/test_review.py
pytest tests/test_advisor.py
```

## Endpoints

- `POST /login` - authenticate with employee ID + password, returns employee name
- `POST /inquiry` - submit an HR inquiry (SSE stream)
- `POST /feedback` - submit a 1–5 star rating for a completed inquiry
- `GET /employees/{employee_id}` - inspect a seeded employee record (debug)
  - Available IDs: E001 through E006
- `GET /policies/<filename>` - serve knowledge base markdown files
