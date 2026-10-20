"""Real provider calls. Skipped by default. Run from backend/ (needs keys + DB):

uv run --env-file ../.env pytest -m live
"""

import os

import pytest
from pydantic import BaseModel

from app.core.llm import TASKS, load_prompt
from app.core.llm.gateway import LLMGateway

pytestmark = [pytest.mark.live, pytest.mark.db]


class Pong(BaseModel):
    answer: str


@pytest.fixture(autouse=True)
def _need_keys():
    # LiteLLM reads keys from os.environ. (It also calls load_dotenv() on import, but we pass
    # --env-file explicitly rather than rely on that.)
    missing = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if not os.environ.get(k)]
    if missing:
        pytest.skip(f"{missing} not in the environment; use `uv run --env-file ../.env`")


def test_each_tier_and_embedding(llm_db):
    gw = LLMGateway(session_factory=llm_db)
    prompt = load_prompt("smoke")
    for task in TASKS:
        out = gw.complete(task, "llm_smoke", prompt.messages(tier=task), Pong, use_cache=False)
        assert out.answer.lower() == "pong"
    result = gw.embed(["FrogRadar live smoke test"], operation="llm_smoke", use_cache=False)
    assert len(result.vectors[0]) == result.dimensions
