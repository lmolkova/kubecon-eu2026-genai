import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.otel import configure_opentelemetry
from backend.database import create_pool, get_employee
from backend.agents.intake import run_intake
from backend.agents.policy import run_policy
from backend.agents.escalation import run_review, run_direct_escalation
from backend.agents.otel_helpers import emit_evaluation_event
from backend.models import ReviewDecision, FeedbackRequest
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

configure_opentelemetry()

logger = logging.getLogger(__name__)

app = FastAPI(title="HR AI Assistant")
FastAPIInstrumentor().instrument_app(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://hr_user:hr_pass@localhost:5432/hr_db"
)
DEMO_PASSWORD = "pas$word1!"
KNOWLEDGE_BASE_PATH = str(Path(__file__).parent / "knowledge_base")

db_pool = None


@app.on_event("startup")
async def startup():
    global db_pool
    db_pool = await create_pool(DATABASE_URL)


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()


# --- SSE helper ---

def sse(event: str, data: dict) -> str:
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


# --- Login endpoint ---

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if password != DEMO_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    record = await get_employee(db_pool, username)
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"employee_id": record["employee_id"], "name": record["name"]}


# --- Main inquiry endpoint ---

@app.post("/inquiry")
async def inquiry(
    employee_id: str = Form(...),
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
):
    """Process an HR inquiry through the 3-agent pipeline, streaming SSE progress."""
    span_ctx = trace.get_current_span().get_span_context()
    inquiry_trace_id = format(span_ctx.trace_id, '032x') if span_ctx.is_valid else None
    inquiry_span_id = format(span_ctx.span_id, '016x') if span_ctx.is_valid else None

    async def generate():
        # 1. Extract file text
        file_contents: list[str] = []
        for f in files:
            if f.filename:
                raw = await f.read()
                try:
                    file_contents.append(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    file_contents.append(f"[Binary file: {f.filename} — cannot display]")

        # 2. Intake agent
        yield sse("progress", {"stage": "intake", "message": "Analysing your inquiry..."})
        try:
            intake = await run_intake(message, file_contents)
        except Exception as e:
            yield sse("error", {"message": f"Intake agent error: {e}"})
            return

        yield sse("intake", intake.model_dump())

        # 3. Direct escalation path (intake flagged it — skip policy agent)
        if intake.route_to_escalation:
            yield sse("progress", {"stage": "review", "message": "Preparing escalation summary..."})
            try:
                review = await run_direct_escalation(inquiry=message, intake=intake)
            except Exception as e:
                yield sse("error", {"message": f"Review agent error: {e}"})
                return
            yield sse("review", review.model_dump())
            yield sse("done", {"message": "Processing complete.", "trace_id": inquiry_trace_id, "span_id": inquiry_span_id})
            return

        # 4. Policy → Review loop (max 2 revision rounds)
        MAX_REVISIONS = 2
        revision_feedback: str | None = None

        for revision_round in range(MAX_REVISIONS + 1):
            stage_msg = (
                "Looking up policies and your HR data..."
                if revision_round == 0
                else f"Revising response (round {revision_round})..."
            )
            yield sse("progress", {"stage": "policy", "message": stage_msg})
            try:
                policy = await run_policy(
                    employee_id=employee_id,
                    db_pool=db_pool,
                    knowledge_base_path=KNOWLEDGE_BASE_PATH,
                    inquiry=message,
                    intake_summary=intake.summary,
                    revision_feedback=revision_feedback,
                )
            except Exception as e:
                yield sse("error", {"message": f"Policy agent error: {e}"})
                return

            yield sse("policy", {**policy.model_dump(), "revision_round": revision_round})

            yield sse("progress", {"stage": "review", "message": "Reviewing the response..."})
            try:
                review = await run_review(
                    inquiry=message,
                    intake=intake,
                    policy=policy,
                    revision_round=revision_round,
                )
            except Exception as e:
                yield sse("error", {"message": f"Review agent error: {e}"})
                return

            yield sse("review", {**review.model_dump(), "revision_round": revision_round})

            if review.decision == ReviewDecision.approve:
                break
            if review.decision == ReviewDecision.escalate:
                break
            # request_revision: loop again with feedback (unless we hit the cap)
            revision_feedback = review.feedback
            if revision_round == MAX_REVISIONS:
                # Ran out of revision rounds — approve whatever we have
                break

        yield sse("done", {"message": "Processing complete.", "trace_id": inquiry_trace_id, "span_id": inquiry_span_id})

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- Feedback endpoint ---

@app.post("/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    """Record user rating (1–5 stars) and optional comment for a completed inquiry."""
    if not 1 <= feedback.rating <= 5:
        raise HTTPException(status_code=422, detail="Rating must be between 1 and 5")
    logger.info(
        "Inquiry feedback received",
        extra={
            "employee_id": feedback.employee_id,
            "rating": feedback.rating,
            "comment": feedback.comment,
        },
    )
    emit_evaluation_event(
        rating=feedback.rating,
        comment=feedback.comment,
        trace_id_hex=feedback.trace_id,
        span_id_hex=feedback.span_id,
    )
    return {"status": "ok"}


# --- Debug endpoint ---

@app.get("/employees/{employee_id}")
async def get_employee_record(employee_id: str):
    record = await get_employee(db_pool, employee_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
            for k, v in record.items()}


# --- Serve knowledge base and frontend (order matters: specific before catch-all) ---

app.mount("/policies", StaticFiles(directory=KNOWLEDGE_BASE_PATH), name="policies")

frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
