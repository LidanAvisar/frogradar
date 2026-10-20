"""Fixtures for LLM gateway tests: a fake LiteLLM, a dedicated migrated DB, gateway factory."""

from collections.abc import Callable, Iterator
from typing import Any

import litellm
import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.core.catalog import LLMRoutingFile, load_llm_routing
from app.core.config import Settings
from app.core.llm.gateway import LLMGateway
from app.core.llm.provider import LiteLLMProvider
from tests.conftest import alembic_config


@pytest.fixture(scope="session")
def llm_engine(make_database: Callable[[], URL]) -> Iterator[Engine]:
    """A database of its own: gateway rows are committed, so they must not leak into other tests."""
    url = make_database()
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def llm_db(llm_engine: Engine) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=llm_engine, expire_on_commit=False)
    yield factory
    with llm_engine.begin() as conn:
        conn.execute(text("TRUNCATE llm_calls, llm_cache, missions, pipeline_runs CASCADE"))


def make_response(content: str, *, model: str, tokens_in: int = 10, tokens_out: int = 5) -> Any:
    return litellm.ModelResponse(
        model=model,
        choices=[
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        usage={
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    )


class FakeLiteLLM:
    """Scripted stand-in for litellm.completion / litellm.embedding.

    Each scripted item is a str (completion content), a list of vectors (embedding),
    or an Exception to raise.
    """

    def __init__(self) -> None:
        self.script: list[Any] = []
        self.calls: list[dict[str, Any]] = []
        self.embed_calls: list[dict[str, Any]] = []

    def queue(self, *items: Any) -> None:
        self.script.extend(items)

    def completion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return make_response(item, model=kwargs["model"])

    def embedding(self, **kwargs: Any) -> Any:
        self.embed_calls.append(kwargs)
        item = self.script.pop(0) if self.script else None
        if isinstance(item, Exception):
            raise item
        dims = kwargs["dimensions"]
        vectors = item or [[float(i % 7) / 7] * dims for i in range(len(kwargs["input"]))]
        return litellm.EmbeddingResponse(
            model=kwargs["model"].split("/", 1)[-1],
            data=[
                {"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)
            ],
            usage={
                "prompt_tokens": 3 * len(kwargs["input"]),
                "total_tokens": 3 * len(kwargs["input"]),
            },
        )


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch) -> FakeLiteLLM:
    fake = FakeLiteLLM()
    monkeypatch.setattr(litellm, "completion", fake.completion)
    monkeypatch.setattr(litellm, "embedding", fake.embedding)
    return fake


def settings_for(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "demo_mode": False,
        "llm_daily_budget_usd": 100.0,
        "llm_max_retries": 3,
        "llm_backoff_base_s": 0.01,
        "llm_timeout_s": 5.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_gateway(llm_db: sessionmaker[Session], sleeps: list[float]) -> Callable[..., LLMGateway]:
    def _make(routing: LLMRoutingFile | None = None, **settings: Any) -> LLMGateway:
        s = settings_for(**settings)
        return LLMGateway(
            settings=s,
            routing=routing or load_llm_routing(),
            session_factory=llm_db,
            provider=LiteLLMProvider(s, sleep=sleeps.append),
        )

    return _make


def routing_with(**task_models: str) -> LLMRoutingFile:
    """Real routing config with some task models replaced, plus optional pricing."""
    data = load_llm_routing().model_dump()
    for task, model in task_models.items():
        data["tasks"][task]["model"] = model
    return LLMRoutingFile.model_validate(data)
