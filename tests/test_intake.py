"""Evals for the intake agent using gpt-4o-mini.

Run with:
    pytest tests/test_intake_evals.py -v
"""
import pytest

from backend.agents.intake import intake_agent, run_intake
from backend.models import InquiryType, Severity

EVAL_MODEL = "openai:gpt-4o-mini"


@pytest.mark.parametrize("message,expected_type,expected_severity,expected_escalation", [
    (
        "How many vacation days do I have left this year?",
        InquiryType.vacation,
        Severity.routine,
        False,
    ),
    (
        "I need to request parental leave starting next month. What is the process?",
        InquiryType.leave,
        Severity.routine,
        False,
    ),
    (
        "My father had a heart attack. I need emergency family leave starting tomorrow.",
        InquiryType.leave,
        Severity.urgent,
        False,
    ),
    (
        "My manager has been making sexual comments about me repeatedly. I feel unsafe.",
        InquiryType.conduct,
        Severity.sensitive,
        True,
    ),
    (
        "I believe I'm being paid significantly less than male colleagues doing the same job.",
        InquiryType.compensation,
        Severity.sensitive,
        True,
    ),
    (
        "I was told my role is being eliminated. Is this wrongful termination?",
        InquiryType.termination,
        Severity.sensitive,
        True,
    ),
])
async def test_intake_classification(
    message, expected_type, expected_severity, expected_escalation
):
    with intake_agent.override(model=EVAL_MODEL):
        result = await run_intake(message, [])

    assert result.inquiry_type == expected_type, (
        f"Input: {message!r}\n"
        f"Expected type={expected_type}, got {result.inquiry_type}"
    )
    assert result.severity == expected_severity, (
        f"Input: {message!r}\n"
        f"Expected severity={expected_severity}, got {result.severity}"
    )
    assert result.route_to_escalation == expected_escalation, (
        f"Input: {message!r}\n"
        f"Expected route_to_escalation={expected_escalation}, got {result.route_to_escalation}"
    )
    assert result.summary, "summary should not be empty"
