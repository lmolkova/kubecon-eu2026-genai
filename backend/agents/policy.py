import json
from pathlib import Path

from pydantic_ai import Agent, RunContext

from backend.models import PolicyDeps, PolicyResult
import backend.database as db
from backend.agents.otel_helpers import run_agent, tool_span

_MODEL = "openai:gpt-4o"

_SYSTEM_PROMPT = """You are the policy and case agent for an HR AI assistant.

Your job is to:
1. Use your tools to look up the employee's HR data (country, role, salary, vacation balance, etc.)
   and to search company policies relevant to the inquiry.
2. Provide a clear, accurate, and empathetic answer to the employee's question.
3. List the exact filenames of the policy documents you referenced in `relevant_policies`
   (e.g. "vacation_policy.md", "leave_policy.md", "compensation_policy.md", "code_of_conduct.md").
   Use only the actual filenames — they will be rendered as clickable links for the employee.
4. Suggest concrete next steps the employee should take.
5. Set needs_escalation=true if the situation requires human HR review — for example:
   - The inquiry involves harassment, discrimination, or retaliation.
   - There is a potential legal or compliance issue.
   - The employee disputes pay in a way that may indicate inequity.
   - The situation involves a manager conflict.
   - The policy is ambiguous and the stakes are high.
   In those cases, also provide a brief escalation_reason.

Always use the employee's actual data when answering. Do not make up numbers.
"""

policy_agent = Agent(
    _MODEL,
    deps_type=PolicyDeps,
    output_type=PolicyResult,
    system_prompt=_SYSTEM_PROMPT,
)


@policy_agent.tool
async def get_employee_info(ctx: RunContext[PolicyDeps]) -> dict:
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


@policy_agent.tool
async def get_manager_info(ctx: RunContext[PolicyDeps], manager_id: str) -> dict:
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


@policy_agent.tool
async def search_policies(ctx: RunContext[PolicyDeps], topic: str) -> str:
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

        span.set_attribute("gen_ai.tool.call.result", result[:4096])  # truncate for span safety
        return result


async def run_policy(
    employee_id: str,
    db_pool,
    knowledge_base_path: str,
    inquiry: str,
    intake_summary: str,
    revision_feedback: str | None = None,
) -> PolicyResult:
    """Run the policy agent for the given employee inquiry.

    If revision_feedback is provided, the agent is asked to revise its previous response
    based on the review agent's critique.
    """
    deps = PolicyDeps(
        employee_id=employee_id,
        db_pool=db_pool,
        knowledge_base_path=knowledge_base_path,
    )

    if revision_feedback:
        prompt = f"""You previously answered an HR inquiry but the review agent requested a revision.

--- Original employee inquiry ---
{inquiry}

Intake summary: {intake_summary}

--- Review agent feedback ---
{revision_feedback}

Please look up the employee's data and relevant policies again as needed, then provide
an improved answer that addresses all of the reviewer's concerns."""
    else:
        prompt = f"""Employee inquiry (already triaged by intake agent):

Intake summary: {intake_summary}

Full employee message:
{inquiry}

Please look up the employee's data and relevant policies, then answer the inquiry."""

    result = await run_agent(
        policy_agent,
        prompt,
        agent_name="policy",
        model_str=_MODEL,
        system_prompt=_SYSTEM_PROMPT,
        deps=deps,
    )
    return result.output
