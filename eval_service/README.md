# Eval Service

An LLM-as-judge evaluation service that continuously monitors HR agent traces and scores response quality.

## Overview

The service polls [Tempo](../docker-compose.yml) for recent traces, extracts agent inputs/outputs, and uses GPT-4o-mini as a judge to evaluate quality. Results are emitted as `gen_ai.evaluation.result` log events linked to the original trace.

## What Is Evaluated

There are two evaluation scenarios depending on what the review agent decided:

### Scenario A: Policy Response (4 metrics)

Runs when the **policy agent produced a response** (regardless of whether review escalated or approved it).

| Metric | Passes when... |
|--------|----------------|
| `response_relevance` | The response directly addresses the employee's actual question |
| `policy_groundedness` | Every factual claim is consistent with the HR knowledge base |
| `response_tone` | The response is professional, empathetic, and appropriate for HR context |
| `answer_completeness` | The response addresses all parts of the question with sufficient detail and next steps |

### Scenario B: Direct Escalation (1 metric)

Runs when the **review agent escalated without a policy response** (intake → review, policy agent skipped).

| Metric | Passes when... |
|--------|----------------|
| `review_decision_correctness` | Escalating to a human HR professional was the right call (e.g., harassment, discrimination, legal threat, safety risk) |

## Prompts Used

### Policy Response Judge

- **Model**: `gpt-4o-mini`, temperature=0, JSON response format
- **System prompt**: Built dynamically by loading all `*.md` files from `backend/knowledge_base/` as ground truth. Instructs the judge to return binary pass/fail for each of the 4 metrics with a one-sentence reason.
- **User content**:
  ```
  Employee inquiry:
  <inquiry extracted from the intake agent span>

  Final response:
  <policy agent's response>

  Full chat history:
  <chat history downloaded from S3 via span attribute gen_ai.output.messages_ref>
  ```

### Escalation Judge

- **Model**: `gpt-4o-mini`, temperature=0, JSON response format
- **System prompt**: Evaluates only `review_decision_correctness` — whether direct escalation was appropriate.
- **User content**:
  ```
  Employee inquiry:
  <inquiry>

  Handoff summary prepared for HR:
  <handoff summary from the review agent>

  Review reasoning:
  <review agent's reasoning>
  ```

## Which Evals Run on Which Paths

```
intake ──► policy ──► review
               │          │
               │      escalate  →  review_decision_correctness
               │
           policy answer exists  →  response_relevance
                                     policy_groundedness
                                     response_tone
                                     answer_completeness
```

The service searches Tempo for spans matching:

```traceql
{span.gen_ai.agent.name="review" && span.gen_ai.operation.name="invoke_agent"}
```

It then walks the trace to find spans from the `intake` and `policy` agents and reconstructs the full conversation context.

## Evaluation Output

Each metric is emitted as an event correlated with the review span:

| Attribute | Value |
|-----------|-------|
| `gen_ai.evaluation.name` | Metric name (e.g., `response_relevance`) |
| `gen_ai.evaluation.score.value` | `1.0` (pass) or `0.0` (fail) |
| `gen_ai.evaluation.explanation` | One-sentence reason from the judge |

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `POLL_INTERVAL_SECONDS` | `120` | How often to search for new traces |
| `LOOKBACK_SECONDS` | `300` | How far back to search for traces |
| `OPENAI_API_KEY` | required | API key for the judge model |

Traces are deduplicated in memory to avoid re-evaluating the same trace across polling cycles.
