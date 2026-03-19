import os

from pydantic_ai import Agent
from backend.models import IntakeResult
from backend.agents.otel_helpers import run_agent

_MODEL = os.environ.get("LLM_MODEL", "openai:gpt-4o")
_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "1"))

_SYSTEM_PROMPT = """You are an HR intake classifier. Classify and route inquiries only - do not answer them.

- type: vacation | leave | compensation | conduct | termination | general
- severity: routine (standard request) | urgent (time-sensitive) | sensitive (legal risk, harassment, discrimination, pay equity, medical)
- summary: one sentence
- missing_info: situation details only (dates, context) - never ask for employee identity
- route_to_escalation: true only for harassment, discrimination, or legal threats
"""

intake_agent = Agent(
    _MODEL,
    output_type=IntakeResult,
    system_prompt=_SYSTEM_PROMPT,
    model_settings={'temperature': _TEMPERATURE, 'max_tokens': 1000},
)


async def run_intake(message: str, file_contents: list[str], employee_id: str | None = None, employee_name: str | None = None) -> IntakeResult:
    """Run the intake agent on an employee inquiry."""
    prefix = ""
    if employee_name or employee_id:
        parts = []
        if employee_name:
            parts.append(f"Name: {employee_name}")
        if employee_id:
            parts.append(f"Employee ID: {employee_id}")
        prefix = "Employee: " + ", ".join(parts) + "\n\n"
    full_input = prefix + message
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
