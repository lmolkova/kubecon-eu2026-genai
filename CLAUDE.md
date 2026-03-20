# CLAUDE.md

## Accessing Message Blobs from S3Mock

Traces in Tempo may contain `gen_ai.input.messages_ref`, `gen_ai.output.messages_ref`, and `gen_ai.system_instructions_ref` — these are S3 URIs pointing to message content stored in the local S3Mock container.

S3Mock runs at `http://localhost:9090`, bucket: `halluchr-chats`. The `local` AWS CLI profile is already configured (`~/.aws/config` + `~/.aws/credentials`).

Fetch a blob by S3 URI (e.g. from a span attribute):

```bash
aws --profile local s3 cp s3://halluchr-chats/<path> -
```

List all blobs:

```bash
aws --profile local s3 ls s3://halluchr-chats/ --recursive
```

## Running Tests

Tests are LLM evals and call the OpenAI API - they cost money. **Do not run automatically.**

Requires `OPENAI_API_KEY` set in a `.env` file at the repo root or as an environment variable.

```bash
pytest tests/
```

Run a specific file:

```bash
pytest tests/test_intake.py
pytest tests/test_review.py
pytest tests/test_advisor.py
```
