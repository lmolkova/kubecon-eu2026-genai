# CLAUDE.md

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
