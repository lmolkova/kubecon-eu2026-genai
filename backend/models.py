from __future__ import annotations

from enum import Enum
from dataclasses import dataclass

from pydantic import BaseModel


class InquiryType(str, Enum):
    vacation = "vacation"
    leave = "leave"
    compensation = "compensation"
    conduct = "conduct"
    termination = "termination"
    general = "general"


class Severity(str, Enum):
    routine = "routine"
    urgent = "urgent"
    sensitive = "sensitive"  # legal/HR risk


class IntakeResult(BaseModel):
    inquiry_type: InquiryType
    severity: Severity
    summary: str  # 1-sentence restatement of the inquiry
    route_to_escalation: bool  # skip advisor agent, go straight to human


class AdvisorResult(BaseModel):
    answer: str
    relevant_policies: list[str]
    suggested_next_steps: list[str]
    needs_escalation: bool
    escalation_reason: str | None = None


class ReviewDecision(str, Enum):
    approve = "approve"             # response is good, deliver to employee
    request_revision = "request_revision"  # advisor should revise with feedback
    escalate = "escalate"           # hand off to a human HR professional


class ReviewResult(BaseModel):
    decision: ReviewDecision
    reason: str                          # why this decision was made
    feedback: str | None = None          # for request_revision: specific issues to fix
    handoff_summary: str | None = None   # for escalate: what the HR human needs to know
    visibility_restriction: bool = False # mark case as confidential
    urgency: str = "normal"              # "normal" | "urgent" | "immediate"


class FeedbackRequest(BaseModel):
    employee_id: str
    rating: int           # 1–5 stars
    comment: str | None = None
    trace_id: str | None = None   # hex trace-id from the inquiry SSE done event
    span_id: str | None = None    # hex span-id from the inquiry SSE done event


# ---- Runtime deps for advisor agent ----

@dataclass
class AdvisorDeps:
    employee_id: str
    db_pool: object  # asyncpg Pool
    knowledge_base_path: str
