"""FrogRadar backend.

LiteLLM reads these at import time, and every app module imports this package first:
- LITELLM_MODE=PRODUCTION: LiteLLM doesn't call load_dotenv() on import. Keys come only from the
  real environment (compose env_file, or `uv run --env-file ../.env` locally), never from a .env
  found by walking up from the working directory.
- LITELLM_LOCAL_MODEL_COST_MAP=True: use the price table bundled with the pinned LiteLLM version
  instead of downloading it from GitHub on every import (no network at import, reproducible costs).

LangGraph pulls in LangSmith: tracing stays off unless explicitly enabled, so no pipeline data
(which includes collected article text) is sent to a third-party tracing service.
"""

import os

os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
