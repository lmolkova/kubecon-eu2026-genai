from pydantic_ai import Agent
from backend.models import ReviewDecision, ReviewResult, IntakeResult, PolicyResult
from backend.agents.otel_helpers import run_agent

_MODEL = "openai:gpt-4o"

_SYSTEM_PROMPT = """You are the review agent for an HR AI assistant.

Your job is to critically evaluate the policy agent's response to an employee HR inquiry
and choose one of three decisions:

**approve** — The response is accurate, complete, empathetic, and appropriate for the
employee to receive. All relevant policies were applied correctly. Next steps are clear.

**request_revision** — The response has problems that the policy agent should fix before
it reaches the employee. Use this when:
- Key facts are missing or wrong (e.g. wrong vacation balance, wrong policy for country)
- The answer is vague, generic, or ignores the specifics of the employee's situation
- Important next steps are omitted
- The tone is inappropriate (too blunt, too dismissive, not empathetic enough)
- A relevant policy was not considered
When requesting revision, provide specific, actionable feedback in the `feedback` field.

**escalate** — The case must go to a human HR professional, regardless of response quality.
Escalate when:
- The inquiry involves harassment, bullying, discrimination, or retaliation allegations
- There is a legal threat or mention of legal action
- The employee requests a medical or disability accommodation
- There is a pay equity / discrimination concern
- There is a direct manager conflict
- The situation involves potential termination or distressed resignation
- The intake agent flagged route_to_escalation
- The policy agent flagged needs_escalation
- The situation requires human empathy or judgment beyond policy

For **escalate**: write a factual `handoff_summary` for the HR professional, set
`visibility_restriction=true` for sensitive cases (harassment, medical, pay equity),
and set urgency to "immediate" (safety/legal risk), "urgent" (time-sensitive), or "normal".

Always set `reason` to briefly explain your decision.
"""

review_agent = Agent(
    _MODEL,
    output_type=ReviewResult,
    system_prompt=_SYSTEM_PROMPT,
)


async def run_review(
    inquiry: str,
    intake: IntakeResult,
    policy: PolicyResult,
    revision_round: int = 0,
) -> ReviewResult:
    """Review the policy agent's response and decide: approve, revise, or escalate."""
    round_note = f" (revision round {revision_round})" if revision_round > 0 else ""

    prompt = f"""Review this HR case{round_note} and evaluate the policy agent's response.

--- Original employee inquiry ---
{inquiry}

--- Intake classification ---
Type: {intake.inquiry_type}
Severity: {intake.severity}
Summary: {intake.summary}
Routed directly to escalation by intake: {intake.route_to_escalation}

--- Policy agent response ---
Answer:
{policy.answer}

Relevant policies cited: {', '.join(policy.relevant_policies) or 'none'}
Suggested next steps: {policy.suggested_next_steps}
Policy agent escalation flag: {policy.needs_escalation}
Policy agent escalation reason: {policy.escalation_reason or 'N/A'}

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
    """Called when intake routes directly to escalation — no policy agent response to review."""
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
