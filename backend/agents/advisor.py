import json
import os
from pathlib import Path

from pydantic_ai import Agent, RunContext

from backend.models import AdvisorDeps, AdvisorResult
import backend.database as db
from backend.agents.otel_helpers import run_agent, tool_span

_MODEL = os.environ.get("LLM_MODEL", "openai:gpt-4o")
_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "1.0"))

_SYSTEM_PROMPT = """You are an HR advisor. Use tools to look up employee data and relevant policies, then answer the inquiry.

- answer: clear, accurate, empathetic; use actual employee data - no invented numbers
- relevant_policies: exact filenames only (e.g. "vacation_policy.md") - rendered as links
- suggested_next_steps: concrete actions
- needs_escalation + escalation_reason: set if harassment, legal risk, pay dispute, manager conflict, or ambiguous high-stakes policy
"""

advisor_agent = Agent(
    _MODEL,
    deps_type=AdvisorDeps,
    output_type=AdvisorResult,
    system_prompt=_SYSTEM_PROMPT,
    model_settings={'temperature': _TEMPERATURE, 'max_tokens': 1000},
)


@advisor_agent.tool
async def get_employee_info(ctx: RunContext[AdvisorDeps]) -> dict:
    """Retrieve the current employee's HR record from the database."""
    with tool_span("get_employee_info", call_id=ctx.tool_call_id) as span:
        record = await db.get_employee(ctx.deps.db_pool, ctx.deps.employee_id)
        if record is None:
            result = {"error": f"No employee found with ID {ctx.deps.employee_id}"}
        else:
            result = {
                k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v)
                for k, v in record.items()
            }
        span.set_attribute("gen_ai.tool.call.result", json.dumps(result))
        return result

@advisor_agent.tool
async def get_employee_info_by_name(ctx: RunContext[AdvisorDeps], employee_name: str) -> dict:
    """Retrieve the current employee's HR record from the database by name."""
    with tool_span(
        "get_employee_info_by_name",
        call_id=ctx.tool_call_id,
        arguments={"employee_name": employee_name},
    ) as span:
        record = await db.get_employee_by_name(ctx.deps.db_pool, employee_name)
        if record is None:
            result = {"error": f"No employee found with name {employee_name}"}
        else:
            result = {
                k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v)
                for k, v in record.items()
            }
        span.set_attribute("gen_ai.tool.call.result", json.dumps(result))
        return result


@advisor_agent.tool
async def get_manager_info(ctx: RunContext[AdvisorDeps], manager_id: str) -> dict:
    """Retrieve a manager's basic HR record by their employee ID."""
    with tool_span(
        "get_manager_info",
        call_id=ctx.tool_call_id,
        arguments={"manager_id": manager_id},
    ) as span:
        record = await db.get_manager(ctx.deps.db_pool, manager_id)
        if record is None:
            result = {"error": f"No manager found with ID {manager_id}"}
        else:
            result = {
                k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v)
                for k, v in record.items()
            }
        span.set_attribute("gen_ai.tool.call.result", json.dumps(result))
        return result


@advisor_agent.tool
async def search_policies(ctx: RunContext[AdvisorDeps], topic: str) -> str:
    """Search company policy documents for content relevant to the given topic.

    Args:
        topic: A keyword or phrase describing what policy area to look up
               (e.g. 'vacation carryover', 'parental leave Germany', 'harassment reporting').
    """
    with tool_span(
        "search_policies",
        call_id=ctx.tool_call_id,
        arguments={"topic": topic},
    ) as span:
        kb_path = Path(ctx.deps.knowledge_base_path)
        results: list[str] = []

        topic_lower = topic.lower()
        for md_file in sorted(kb_path.glob("*.md")):
            content = md_file.read_text()
            if any(word in content.lower() for word in topic_lower.split()):
                results.append(f"## {md_file.stem.replace('_', ' ').title()}\n\n{content}")

        if not results:
            all_policies = []
            for md_file in sorted(kb_path.glob("*.md")):
                all_policies.append(
                    f"## {md_file.stem.replace('_', ' ').title()}\n\n{md_file.read_text()}"
                )
            result = "\n\n---\n\n".join(all_policies) if all_policies else "No policy documents found."
        else:
            result = "\n\n---\n\n".join(results)

        span.set_attribute("gen_ai.tool.call.result", result)  # truncate for span safety
        return result


async def run_advisor(
    employee_id: str,
    db_pool,
    knowledge_base_path: str,
    inquiry: str,
    intake_summary: str,
    feedback: str | None = None,
    message_history=None,
) -> tuple[AdvisorResult, list]:
    """Run the advisor agent for the given employee inquiry.

    On the first call, pass inquiry + intake_summary with no message_history.
    On revision calls, pass the feedback as the new user turn along with
    message_history from the prior run so the model sees the full conversation.

    Returns (AdvisorResult, all_messages) so the caller can thread messages
    into the next revision round.
    """
    deps = AdvisorDeps(
        employee_id=employee_id,
        db_pool=db_pool,
        knowledge_base_path=knowledge_base_path,
    )

    if message_history is not None:
        # Revision round: just send the reviewer's feedback as a new user turn.
        prompt = feedback
    else:
        prompt = f"""Employee inquiry (already triaged by intake agent):

--- Employee inquiry ---
{inquiry}

Intake summary: {intake_summary}

Please look up the employee's data and relevant policies, then answer the inquiry."""

    result = await run_agent(
        advisor_agent,
        prompt,
        agent_name="advisor",
        model_str=_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        deps=deps,
        message_history=message_history,
    )
    return result.output, result.all_messages()
