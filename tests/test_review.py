"""Evals for the review agent using gpt-4o-mini.

Run with:
    pytest tests/test_review_evals.py -v
"""
import pytest

from backend.agents.escalation import review_agent, run_review, run_direct_escalation
from backend.models import (
    IntakeResult, AdvisorResult, ReviewDecision,
    InquiryType, Severity,
)

EVAL_MODEL = "openai:gpt-4o-mini"


def make_intake(
    inquiry_type=InquiryType.vacation,
    severity=Severity.routine,
    summary="Employee asked about vacation balance.",
    route_to_escalation=False,
) -> IntakeResult:
    return IntakeResult(
        inquiry_type=inquiry_type,
        severity=severity,
        summary=summary,
        route_to_escalation=route_to_escalation,
    )


def make_advisor_response(
    answer: str,
    relevant_policies: list[str] | None = None,
    next_steps: list[str] | None = None,
    needs_escalation: bool = False,
    escalation_reason: str | None = None,
) -> AdvisorResult:
    return AdvisorResult(
        answer=answer,
        relevant_policies=relevant_policies or ["vacation_policy.md"],
        suggested_next_steps=next_steps or ["Submit a leave request in the HR portal."],
        needs_escalation=needs_escalation,
        escalation_reason=escalation_reason,
    )


# --- approve / request_revision cases ---

async def test_review_approves_clear_policy_answer():
    """A complete, accurate, empathetic answer to a routine question should be approved."""
    intake = make_intake(summary="Employee asked how many vacation days they have left.")
    advisor_response = make_advisor_response(
        answer=(
            "You currently have 12 vacation days remaining for this year. "
            "Days must be used by December 31 and cannot be carried over. "
            "Please submit requests at least 2 weeks in advance via the HR portal."
        ),
        next_steps=["Log into the HR portal and submit a vacation request."],
    )

    with review_agent.override(model=EVAL_MODEL):
        result = await run_review(
            inquiry="How many vacation days do I have left?",
            intake=intake,
            response=advisor_response,
        )

    assert result.decision == ReviewDecision.approve, (
        f"Expected approve, got {result.decision}. Reason: {result.reason}"
    )


async def test_review_requests_revision_for_vague_answer():
    """A generic answer that ignores the employee's specific situation should be revised."""
    intake = make_intake(summary="Employee asked about parental leave entitlement in Germany.")
    advisor_response = make_advisor_response(
        answer="We offer parental leave as per local laws. Please check the leave policy document.",
        relevant_policies=["leave_policy.md"],
        next_steps=["Read the policy."],
    )

    with review_agent.override(model=EVAL_MODEL):
        result = await run_review(
            inquiry="I'm based in Germany. How many weeks of parental leave am I entitled to?",
            intake=intake,
            response=advisor_response,
        )

    assert result.decision == ReviewDecision.request_revision, (
        f"Expected request_revision, got {result.decision}. Reason: {result.reason}"
    )
    assert result.feedback, "feedback should be non-empty when requesting revision"


# --- escalation cases ---

@pytest.mark.parametrize("inquiry,intake_kwargs,advisor_kwargs", [
    (
        "My manager groped me at the office party last week.",
        dict(
            inquiry_type=InquiryType.conduct,
            severity=Severity.sensitive,
            summary="Employee reporting physical harassment by their manager.",
        ),
        dict(
            answer="We take harassment seriously. Please report through official channels.",
            needs_escalation=True,
            escalation_reason="Physical harassment allegation involving a manager.",
        ),
    ),
    (
        "I think I'm being paid less than colleagues because I'm a woman. "
        "I'm considering legal action.",
        dict(
            inquiry_type=InquiryType.compensation,
            severity=Severity.sensitive,
            summary="Employee alleging pay discrimination and mentioning legal action.",
        ),
        dict(
            answer="Pay equity is important. We will review your compensation.",
            needs_escalation=True,
            escalation_reason="Pay discrimination allegation with legal threat.",
        ),
    ),
])
async def test_review_escalates_sensitive_cases(inquiry, intake_kwargs, advisor_kwargs):
    intake = make_intake(**intake_kwargs)
    advisor_response = make_advisor_response(**advisor_kwargs)

    with review_agent.override(model=EVAL_MODEL):
        result = await run_review(inquiry=inquiry, intake=intake, response=advisor_response)

    assert result.decision == ReviewDecision.escalate, (
        f"Expected escalate, got {result.decision}. Reason: {result.reason}"
    )
    assert result.handoff_summary, "handoff_summary should be set when escalating"


async def test_direct_escalation_always_escalates():
    """run_direct_escalation must always produce decision=escalate."""
    intake = make_intake(
        inquiry_type=InquiryType.conduct,
        severity=Severity.sensitive,
        summary="Employee reporting discrimination by manager.",
        route_to_escalation=True,
    )

    with review_agent.override(model=EVAL_MODEL):
        result = await run_direct_escalation(
            inquiry="My manager has been discriminating against me because of my religion.",
            intake=intake,
        )

    assert result.decision == ReviewDecision.escalate
    assert result.handoff_summary, "handoff_summary should be set for direct escalation"
