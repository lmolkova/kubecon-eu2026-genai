# HR AI Assistant

A demo HR AI assistant built with [pydantic-ai](https://ai.pydantic.dev/), FastAPI, and PostgreSQL, with full OpenTelemetry observability (traces, metrics, logs) exported to a local Grafana LGTM stack.

## Architecture

```
Employee (browser)
   │  POST /inquiry (multipart/form-data)
   ▼
FastAPI backend
   │
   ├── 1. Intake Agent        — classify, assess severity, route
   │
   ├── [direct escalation path if intake.route_to_escalation=true]
   │
   ├── 2. Policy Agent        — look up HR data + policies, answer
   │        ▲  │ revision feedback (up to 2 rounds)
   │        │  ▼
   └── 3. Review Agent        — approve / request revision / escalate
            │
            ▼
       PostgreSQL (employee records)
       Knowledge Base (markdown policy files)
```

Results are streamed back to the browser via Server-Sent Events (SSE).

## Observability

The backend emits OpenTelemetry traces, metrics, and logs via OTLP gRPC.
Docker Compose includes [Grafana LGTM](https://github.com/grafana/docker-otel-lgtm) (Loki + Grafana + Tempo + Mimir), so everything is available out of the box at **[localhost:3000](http://localhost:3000)** (no login required).

## Quick Start

### Prerequisites

- Python 3.12+
- Docker + Docker Compose
- An OpenAI API key

### 1. Start PostgreSQL and the Grafana LGTM stack

```bash
docker-compose up postgres lgtm -d
```

### 2. Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 3. Set environment variables

```bash
export OPENAI_API_KEY=sk-...
export DATABASE_URL=postgresql://hr_user:hr_pass@localhost:5432/hr_db
export OTEL_SERVICE_NAME=hr-ai-assistant
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
export OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=span_only
```

### 4. Run the backend

```bash
uvicorn backend.main:app --reload
```

The app (and the frontend) is served at **[localhost:8000](http://localhost:8000)**.
Grafana is available at **[localhost:3000](http://localhost:3000)**.

### Run everything with Docker Compose

```bash
OPENAI_API_KEY=sk-... docker-compose up --build
```

## Test Scenarios

Login with employee ID as username and `pas$word1!` as password.

| Employee | Inquiry | Expected outcome |
|----------|---------|-----------------|
| E001 | "How many vacation days do I have left?" | Policy agent answers with real balance; no escalation |
| E002 | "I need emergency sick leave starting tomorrow" | Urgent; escalation agent flags it |
| E003 | "My manager made inappropriate comments about my gender" | Immediate escalation, confidential flag |
| E004 | "Can I get a salary raise? I think I'm underpaid compared to peers." | Policy agent explains process; potential escalation if pay equity concern |

## Endpoints

- `POST /login` — authenticate with employee ID + password, returns employee name
- `POST /inquiry` — submit an HR inquiry (SSE stream)
- `POST /feedback` — submit a 1–5 star rating for a completed inquiry
- `GET /employees/{employee_id}` — inspect a seeded employee record (debug)
  - Available IDs: E001 through E006
- `GET /policies/<filename>` — serve knowledge base markdown files
