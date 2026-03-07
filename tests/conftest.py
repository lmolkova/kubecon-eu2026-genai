import os
from pathlib import Path

# Load .env from repo root so OPENAI_API_KEY is available for real LLM evals.
# Set a placeholder first so pydantic-ai can instantiate default clients even
# when the key is missing (e.g. CI without credentials).
os.environ.setdefault("OPENAI_API_KEY", "test-placeholder")

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file, override=True)
