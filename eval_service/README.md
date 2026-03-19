# Eval Service

An LLM-as-judge evaluation service that continuously monitors HR agent traces and scores response quality.

## Overview

The service polls [Tempo](../docker-compose.yml) for recent traces, extracts agent inputs/outputs, and uses `gpt-4o-mini` as a judge to evaluate quality. Results are emitted as `gen_ai.evaluation.result` log events linked to the original trace.

## What Is Evaluated

Every `invoke_workflow` span is evaluated with **all** metrics - both the policy judge and the escalation judge run on every trace.

| Metric | Passes when... |
| --- | --- |
| `workflow_health` | The workflow span has no error |
| `response_relevance` | The response directly addresses the employee's actual question |
| `policy_groundedness` | Every factual claim is consistent with the HR knowledge base |
| `response_tone` | The response is professional, empathetic, and appropriate for HR context |
| `answer_completeness` | The response addresses all parts of the question with sufficient detail and next steps |
| `review_decision_correctness` | The routing decision was correct - escalation for serious cases (harassment, legal risk), direct answer for routine ones |

## Prompts Used

### Policy Response Judge

- **Model**: `gpt-4o-mini`, temperature=0, JSON response format
- **System prompt**: Built dynamically by loading all `*.md` files from `backend/knowledge_base/` as ground truth. Instructs the judge to return binary pass/fail for each of the 4 quality metrics with a one-sentence reason.
- **User content**:
  ```
  Employee inquiry:
  <gen_ai.input.messages from the workflow span, fetched from S3 if stored via _ref>

  Final response:
  <gen_ai.output.messages from the workflow span, fetched from S3 if stored via _ref>
  ```

### Escalation Judge

- **Model**: `gpt-4o-mini`, temperature=0, JSON response format
- **System prompt**: Evaluates `review_decision_correctness` - whether escalating to a human vs. answering directly was the right call.
- **User content**:
  ```
  Employee inquiry:
  <gen_ai.input.messages>

  Actual response:
  <gen_ai.output.messages>
  ```

## How Traces Are Found

The service searches Tempo for spans matching:

```traceql
{resource.service.name="hallucHR" && span.gen_ai.operation.name="invoke_workflow"}
```

It then evaluates each matching span directly using its `gen_ai.input.messages` and `gen_ai.output.messages` attributes (falling back to S3 via `_ref` variants).

## Evaluation Output

Each metric is emitted as an event on the `invoke_workflow` span:

| Attribute | Value |
| --- | --- |
| `gen_ai.evaluation.name` | Metric name (e.g., `response_relevance`) |
| `gen_ai.evaluation.score.value` | `1.0` (pass) or `0.0` (fail) |
| `gen_ai.evaluation.explanation` | One-sentence reason from the judge |

## Configuration

| Env var | Default | Description |
| --- | --- | --- |
| `EVAL_INTERVAL_SECONDS` | `10` | How often to poll for new traces |
| `EVAL_LOOKBACK_SECONDS` | `60` | How far back to search on each poll |
| `EVAL_INITIAL_LOOKBACK_SECONDS` | `3600` | How far back to search on first start (before cursor exists) |
| `OPENAI_API_KEY` | required | API key for the judge model (falls back from `OPENAI_API_KEY_EVAL`) |

Evaluated spans are deduplicated using a cursor file (`eval_cursor.txt`) that persists the last processed timestamp across restarts. An in-memory set additionally prevents re-evaluation within the same process run.
