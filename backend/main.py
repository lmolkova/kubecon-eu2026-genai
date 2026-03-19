import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Form, Query, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.otel import configure_opentelemetry
from backend.database import create_pool, get_employee
from backend.agents.intake import run_intake
from backend.agents.advisor import run_advisor
from backend.agents.escalation import run_review, run_direct_escalation
from backend.agents.otel_helpers import emit_evaluation_event, workflow_span
from backend.models import ReviewDecision, FeedbackRequest, IntakeResult, InquiryType, Severity
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

configure_opentelemetry()

logger = logging.getLogger(__name__)

ESCALATION_MESSAGE = "Your inquiry has been forwarded to an HR professional who will follow up with you directly."


def _finish(*, intake, response: str, escalated: bool = False) -> dict:
    """Set workflow output on the current span and attach trace/span IDs to the response."""
    span = trace.get_current_span()
    span.set_attribute(
        "gen_ai.output.messages",
        json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": response}], "finish_reason": "stop"}]),
    )
    ctx = span.get_span_context()
    return {
        "intake": intake.model_dump(),
        "response": response,
        "escalated": escalated,
        "trace_id": format(ctx.trace_id, '032x') if ctx.is_valid else None,
        "span_id": format(ctx.span_id, '016x') if ctx.is_valid else None,
    }

app = FastAPI(title="HallucHR")
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
    chaos: bool = Query(False),
):
    """Process an HR inquiry through the 3-agent pipeline."""
    # 1. Extract file text
    file_contents: list[str] = []
    for f in files:
        if f.filename:
            raw = await f.read()
            try:
                file_contents.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                file_contents.append(f"[Binary file: {f.filename} - cannot display]")

    employee_record = await get_employee(db_pool, employee_id)
    employee_name = employee_record["name"] if employee_record else None

    with workflow_span("hallucHR", user_input=message) as wf:
        # 2. Intake agent (skipped in chaos mode)
        if chaos:
            intake = IntakeResult(
                inquiry_type=InquiryType.general,
                severity=Severity.routine,
                summary=message,
                route_to_escalation=False,
            )
        else:
            intake = await run_intake(message, file_contents, employee_id=employee_id, employee_name=employee_name)

        # 3. Direct escalation path (intake flagged it - skip advisor agent)
        if intake.route_to_escalation:
            await run_direct_escalation(inquiry=message, intake=intake)
            wf.output = ESCALATION_MESSAGE
            return _finish(intake=intake, response=ESCALATION_MESSAGE, escalated=True)

        # 4. Advisor → Review loop (max 2 revision rounds)
        MAX_REVISIONS = 2
        advisor_messages = None
        feedback = None

        for revision_round in range(MAX_REVISIONS):
            response, advisor_messages = await run_advisor(
                employee_id=employee_id,
                db_pool=db_pool,
                knowledge_base_path=KNOWLEDGE_BASE_PATH,
                inquiry=message,
                intake_summary=intake.summary,
                feedback=feedback,
                message_history=advisor_messages,
            )
            review = await run_review(
                inquiry=message,
                intake=intake,
                response=response,
            )

            if review.decision == ReviewDecision.approve:
                wf.output = response.answer
                return _finish(intake=intake, response=response.answer)

            if review.decision == ReviewDecision.escalate or revision_round == MAX_REVISIONS - 1:
                wf.output = ESCALATION_MESSAGE
                return _finish(intake=intake, response=ESCALATION_MESSAGE, escalated=True)

            feedback = review.feedback


# --- Feedback endpoint ---

@app.post("/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    """Record user rating (1–5 stars) and optional comment for a completed inquiry."""
    if not 1 <= feedback.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
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
