from pydantic_ai import Agent
from backend.models import IntakeResult
from backend.agents.otel_helpers import run_agent

_MODEL = "openai:gpt-4o"

_SYSTEM_PROMPT = """You are the intake agent for an HR AI assistant.

Your job is to:
1. Understand what the employee is asking or reporting.
2. Classify the inquiry type: vacation, leave, compensation, conduct, termination, or general.
3. Assess severity:
   - "routine": standard policy question or administrative request
   - "urgent": time-sensitive but not legally risky (e.g., emergency leave needed tomorrow)
   - "sensitive": involves legal risk, protected characteristics, harassment, discrimination,
     retaliation, pay equity complaints, wrongful termination concerns, or medical accommodations
4. Write a one-sentence summary of the inquiry.
5. List any information that is missing and would be needed to fully address the request.
   Note: the employee's identity is already known — never list employee ID, name, or contact
   details as missing. Only flag missing details about the situation itself (e.g. dates, context).
6. Set route_to_escalation=true ONLY if the situation is so sensitive that it should bypass
   the policy agent and go directly to a human HR representative (e.g., harassment report,
   discrimination allegation, threat of legal action).

Be concise and accurate. Do not attempt to answer the HR question — just classify and route it.
"""

intake_agent = Agent(
    _MODEL,
    output_type=IntakeResult,
    system_prompt=_SYSTEM_PROMPT,
)


async def run_intake(message: str, file_contents: list[str]) -> IntakeResult:
    """Run the intake agent on an employee inquiry."""
    full_input = message
    if file_contents:
        attachments = "\n\n".join(
            f"--- Attached file {i+1} ---\n{content}"
            for i, content in enumerate(file_contents)
        )
        full_input = f"{message}\n\n{attachments}"

    result = await run_agent(
        intake_agent,
        full_input,
        agent_name="intake",
        model_str=_MODEL,
        system_prompt=_SYSTEM_PROMPT,
    )
    return result.output
