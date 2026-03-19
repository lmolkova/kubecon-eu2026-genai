import os

from pydantic_ai import Agent
from backend.models import ReviewDecision, ReviewResult, IntakeResult, AdvisorResult
from backend.agents.otel_helpers import run_agent

_MODEL = os.environ.get("LLM_MODEL", "openai:gpt-4o-mini")
_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "1.5"))

_SYSTEM_PROMPT = """You are an HR review agent. Evaluate the advisor's response and decide:

**approve** — accurate, complete, empathetic, correct policies applied, clear next steps.

**request_revision** — use when facts are wrong, answer is vague/generic, next steps missing, tone is off, or a relevant policy was missed. Give specific actionable feedback.

**escalate** — required for: harassment/discrimination/retaliation, legal threats, medical accommodations, pay equity concerns, manager conflicts, potential termination, or when either agent flagged escalation. Write a factual handoff_summary, set visibility_restriction=true for sensitive cases, set urgency (immediate/urgent/normal).

Always set `reason`.
"""

review_agent = Agent(
    _MODEL,
    output_type=ReviewResult,
    system_prompt=_SYSTEM_PROMPT,
    model_settings={'temperature': _TEMPERATURE, 'max_tokens': 1000},
)


async def run_review(
    inquiry: str,
    intake: IntakeResult,
    response: AdvisorResult,
) -> ReviewResult:
    """Review the advisor's response and decide: approve, revise, or escalate."""

    prompt = f"""Review this HR case and evaluate the advisor's response.

--- Original employee inquiry ---
{inquiry}

--- Intake classification ---
Type: {intake.inquiry_type}
Severity: {intake.severity}
Summary: {intake.summary}
Routed directly to escalation by intake: {intake.route_to_escalation}

--- Advisor response ---
Answer:
{response.answer}

Relevant policies cited: {', '.join(response.relevant_policies) or 'none'}
Suggested next steps: {response.suggested_next_steps}
Advisor escalation flag: {response.needs_escalation}
Advisor escalation reason: {response.escalation_reason or 'none'}

---
Decide: approve, request_revision (with specific feedback), or escalate."""

    result = await run_agent(
        review_agent,
        prompt,
        agent_name="review",
        model_str=_MODEL,
        system_prompt=_SYSTEM_PROMPT,
    )
    return result.output


async def run_direct_escalation(inquiry: str, intake: IntakeResult) -> ReviewResult:
    """Called when intake routes directly to escalation — no advisor response to review."""
    prompt = f"""An HR inquiry has been flagged for immediate escalation by the intake agent.
Prepare a handoff summary for the human HR professional.

--- Employee inquiry ---
{inquiry}

--- Intake classification ---
Type: {intake.inquiry_type}
Severity: {intake.severity}
Summary: {intake.summary}

The decision must be `escalate`. Write a clear handoff_summary and set appropriate urgency."""

    result = await run_agent(
        review_agent,
        prompt,
        agent_name="review",
        model_str=_MODEL,
        system_prompt=_SYSTEM_PROMPT,
    )
    # Ensure decision is escalate regardless of what the model returns
    output = result.output
    output.decision = ReviewDecision.escalate
    return output
