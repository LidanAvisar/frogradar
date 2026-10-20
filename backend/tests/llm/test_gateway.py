"""Gateway behaviour against a real (throwaway) Postgres, with LiteLLM faked."""

import logging
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import litellm
import pytest
from litellm import exceptions as lx
from pydantic import BaseModel
from sqlalchemy import select

from app.core.llm import (
    BudgetExceeded,
    DemoCacheMiss,
    LLMError,
    MissionBudgetExceeded,
    ProviderError,
    StructuredOutputError,
    llm_context,
)
from app.core.llm.provider import LiteLLMProvider
from app.db.enums import RunTrigger
from app.db.models import LLMCache, LLMCall, Mission, PipelineRun
from tests.llm.conftest import routing_with, settings_for

pytestmark = pytest.mark.db

MSGS = [{"role": "system", "content": "Classify."}, {"role": "user", "content": "Snyk ships X"}]
GOOD = '{"answer": "threat", "score": 4}'


class Answer(BaseModel):
    answer: str
    score: int


def _rows(db) -> list[LLMCall]:
    with db() as s:
        return list(s.scalars(select(LLMCall).order_by(LLMCall.id)))


def _cache_rows(db) -> list[LLMCache]:
    with db() as s:
        return list(s.scalars(select(LLMCache)))


def _schema_support(monkeypatch, supported: bool) -> None:
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model, **_: supported)


def _rate_limit() -> Exception:
    return lx.RateLimitError("slow down", llm_provider="anthropic", model="m")


def _auth_error() -> Exception:
    return lx.AuthenticationError(
        "Incorrect API key provided: sk-test-SECRET1234567890", llm_provider="openai", model="m"
    )


# ---------- structured output ----------


def test_schema_path_sends_response_format_and_logs_row(
    make_gateway, fake_litellm, llm_db, monkeypatch
):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(GOOD)
    gw = make_gateway()

    out = gw.complete("bulk", "enrich_signal", MSGS, Answer, prompt_version="enrich_signal.v1")

    assert out == Answer(answer="threat", score=4)
    call = fake_litellm.calls[0]
    assert call["response_format"]["json_schema"]["schema"] == Answer.model_json_schema()
    assert call["model"] == gw.routing.tasks.bulk.model
    assert call["timeout"] == 5.0 and call["num_retries"] == 0
    [row] = _rows(llm_db)
    assert (row.task, row.operation, row.prompt_version) == (
        "bulk",
        "enrich_signal",
        "enrich_signal.v1",
    )
    assert row.success and not row.cache_hit and row.attempt == 1
    assert (row.tokens_in, row.tokens_out) == (10, 5)
    assert row.cost_usd is not None and row.cost_usd > 0


def test_text_path_parses_fenced_json(make_gateway, fake_litellm, monkeypatch):
    _schema_support(monkeypatch, False)
    fake_litellm.queue(f"```json\n{GOOD}\n```")

    out = make_gateway().complete("verify", "verify_summary", MSGS, Answer)

    assert out.score == 4
    call = fake_litellm.calls[0]
    assert "response_format" not in call
    assert '"score"' in call["messages"][0]["content"]  # schema described in the system prompt


def test_bad_json_is_retried_with_the_validation_error(
    make_gateway, fake_litellm, llm_db, monkeypatch
):
    _schema_support(monkeypatch, True)
    fake_litellm.queue('{"answer": "threat", "score": "very high"}', GOOD)

    out = make_gateway().complete("bulk", "enrich_signal", MSGS, Answer)

    assert out.score == 4
    retry_msgs = fake_litellm.calls[1]["messages"]
    assert retry_msgs[-2]["role"] == "assistant"
    assert "failed validation" in retry_msgs[-1]["content"] and "score" in retry_msgs[-1]["content"]
    first, second = _rows(llm_db)
    assert (first.attempt, first.success) == (1, False) and "validation" in first.error
    assert (second.attempt, second.success) == (2, True)


def test_gives_up_after_two_validation_retries(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    fake_litellm.queue("nope", "still nope", '{"answer": 1}')

    with pytest.raises(StructuredOutputError, match="3 times"):
        make_gateway().complete("bulk", "enrich_signal", MSGS, Answer)

    assert len(fake_litellm.calls) == 3
    assert [r.success for r in _rows(llm_db)] == [False, False, False]
    assert _cache_rows(llm_db) == []  # failures are never cached


# ---------- reliability ----------


def test_rate_limit_is_retried_with_backoff(
    make_gateway, fake_litellm, llm_db, sleeps, monkeypatch
):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(_rate_limit(), GOOD)

    make_gateway().complete("bulk", "enrich_signal", MSGS, Answer)

    assert len(fake_litellm.calls) == 2 and len(sleeps) == 1
    failed, ok = _rows(llm_db)
    assert not failed.success and failed.error.startswith("RateLimitError")
    assert failed.cost_usd == 0 and ok.success


def test_retries_run_out(make_gateway, fake_litellm, llm_db, sleeps, monkeypatch):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(*[_rate_limit() for _ in range(3)])

    with pytest.raises(ProviderError, match="after 3 attempts") as exc:
        make_gateway(llm_max_retries=2).complete("bulk", "enrich_signal", MSGS, Answer)

    assert exc.value.retryable
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]  # exponential
    assert len(_rows(llm_db)) == 3


def test_retry_after_header_is_honoured(make_gateway, fake_litellm, sleeps, monkeypatch):
    _schema_support(monkeypatch, True)
    resp = httpx.Response(
        429, headers={"retry-after": "7"}, request=httpx.Request("POST", "http://x")
    )
    err = lx.RateLimitError("slow down", llm_provider="openai", model="m", response=resp)
    fake_litellm.queue(err, GOOD)

    make_gateway().complete("bulk", "enrich_signal", MSGS, Answer)

    assert sleeps == [7.0]


def test_auth_error_is_not_retried_and_key_is_not_logged(
    make_gateway, fake_litellm, llm_db, sleeps, monkeypatch, caplog
):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(_auth_error())

    with caplog.at_level(logging.DEBUG), pytest.raises(ProviderError) as exc:
        make_gateway().complete("bulk", "enrich_signal", MSGS, Answer)

    assert not exc.value.retryable
    assert len(fake_litellm.calls) == 1 and sleeps == []
    [row] = _rows(llm_db)
    assert not row.success and row.error.startswith("AuthenticationError")
    for text in (row.error, str(exc.value), caplog.text):
        assert "SECRET1234567890" not in text


def test_failure_row_survives_caller_rollback(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(_auth_error())
    with llm_db() as caller:
        caller.begin()
        with pytest.raises(ProviderError):
            make_gateway().complete("bulk", "enrich_signal", MSGS, Answer)
        caller.rollback()
    assert len(_rows(llm_db)) == 1


# ---------- cache ----------


def test_cache_miss_then_hit(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(GOOD)
    gw = make_gateway()

    first = gw.complete("bulk", "enrich_signal", MSGS, Answer, prompt_version="p.v1")
    second = gw.complete("bulk", "enrich_signal", MSGS, Answer, prompt_version="p.v1")

    assert first == second and len(fake_litellm.calls) == 1
    miss, hit = _rows(llm_db)
    assert hit.cache_hit and hit.cost_usd == 0 and hit.cache_key == miss.cache_key
    assert (hit.tokens_in, hit.tokens_out) == (10, 5)
    [entry] = _cache_rows(llm_db)
    assert entry.hit_count == 1 and entry.response == {"answer": "threat", "score": 4}
    assert entry.prompt_version == "p.v1" and entry.kind == "completion"


def test_use_cache_false_bypasses_cache(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(GOOD, GOOD)
    gw = make_gateway()
    gw.complete("bulk", "op", MSGS, Answer, use_cache=False)
    gw.complete("bulk", "op", MSGS, Answer, use_cache=False)
    assert len(fake_litellm.calls) == 2 and _cache_rows(llm_db) == []


# ---------- cost ----------


def test_pricing_fallback_from_yaml(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    routing = routing_with(bulk="anthropic/unknown-model-x")
    data = routing.model_dump()
    data["pricing"] = {
        "anthropic/unknown-model-x": {"input_per_mtok_usd": 1.0, "output_per_mtok_usd": 2.0}
    }
    routing = type(routing).model_validate(data)
    fake_litellm.queue(GOOD)

    make_gateway(routing=routing).complete("bulk", "op", MSGS, Answer)

    [row] = _rows(llm_db)
    assert row.cost_usd == Decimal("0.0000200000")  # 10 * 1/1e6 + 5 * 2/1e6


def test_unknown_cost_is_null_and_warned(make_gateway, fake_litellm, llm_db, monkeypatch, caplog):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(GOOD)

    with caplog.at_level(logging.WARNING):
        make_gateway(routing=routing_with(bulk="anthropic/unknown-model-x")).complete(
            "bulk", "op", MSGS, Answer
        )

    [row] = _rows(llm_db)
    assert row.cost_usd is None and row.success
    assert "cost unknown for model anthropic/unknown-model-x" in caplog.text


# ---------- budget ----------


def test_daily_budget_exceeded(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    with llm_db.begin() as s:
        s.add(LLMCall(task="bulk", operation="earlier", model="m", cost_usd=Decimal("2.5")))

    with pytest.raises(BudgetExceeded, match="daily LLM budget"):
        make_gateway(llm_daily_budget_usd=2.0).complete("bulk", "op", MSGS, Answer)

    assert fake_litellm.calls == []


def test_mission_budget_exceeded(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    with llm_db.begin() as s:
        mission = Mission(
            title="t", brief="b", budget_usd=Decimal("0.01"), max_steps=5, created_by="a"
        )
        s.add(mission)
        s.flush()
        s.add(LLMCall(task="synthesis", operation="research", model="m", cost_usd=Decimal("0.02"),
                      mission_id=mission.id))  # fmt: skip
        mission_id = mission.id

    with llm_context(mission_id=mission_id), pytest.raises(MissionBudgetExceeded):
        make_gateway().complete("synthesis", "research", MSGS, Answer)

    # Outside the mission, the daily budget still allows the call.
    fake_litellm.queue(GOOD)
    make_gateway().complete("synthesis", "research", MSGS, Answer)
    assert len(fake_litellm.calls) == 1


# ---------- demo mode ----------


@pytest.fixture
def forbidden_litellm(monkeypatch):
    """litellm.completion / embedding mocks that fail the test if anything calls them."""
    completion = MagicMock(side_effect=AssertionError("litellm.completion called in demo mode"))
    embedding = MagicMock(side_effect=AssertionError("litellm.embedding called in demo mode"))
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(litellm, "embedding", embedding)
    return completion, embedding


def test_demo_mode_never_calls_providers(make_gateway, forbidden_litellm, llm_db):
    completion, embedding = forbidden_litellm
    gw = make_gateway(demo_mode=True)

    with pytest.raises(DemoCacheMiss, match="enrich_signal"):
        gw.complete("bulk", "enrich_signal", MSGS, Answer)
    with pytest.raises(DemoCacheMiss):
        gw.complete("bulk", "enrich_signal", MSGS, Answer, use_cache=False)
    with pytest.raises(DemoCacheMiss):
        gw.embed(["hello"])
    # Defense in depth: the provider layer refuses on its own too.
    provider = LiteLLMProvider(settings_for(demo_mode=True))
    with pytest.raises(DemoCacheMiss):
        provider.complete(
            model="m", messages=MSGS, max_tokens=10, temperature=0, response_format=None
        )
    with pytest.raises(DemoCacheMiss):
        provider.embed(model="m", texts=["x"], dimensions=8)

    completion.assert_not_called()
    embedding.assert_not_called()
    rows = _rows(llm_db)
    assert len(rows) == 3 and all(not r.success and "demo cache miss" in r.error for r in rows)


def test_demo_mode_serves_from_cache(make_gateway, fake_litellm, monkeypatch):
    _schema_support(monkeypatch, True)
    fake_litellm.queue(GOOD)
    make_gateway().complete("bulk", "enrich_signal", MSGS, Answer)
    make_gateway().embed(["hello"])
    monkeypatch.setattr(litellm, "completion", MagicMock(side_effect=AssertionError))
    monkeypatch.setattr(litellm, "embedding", MagicMock(side_effect=AssertionError))

    demo = make_gateway(demo_mode=True)
    assert demo.complete("bulk", "enrich_signal", MSGS, Answer).score == 4
    assert len(demo.embed(["hello"]).vectors) == 1


# ---------- context ----------


def test_rows_carry_run_and_mission_ids(make_gateway, fake_litellm, llm_db, monkeypatch):
    _schema_support(monkeypatch, True)
    with llm_db.begin() as s:
        run = PipelineRun(trigger=RunTrigger.MANUAL)
        mission = Mission(
            title="t", brief="b", budget_usd=Decimal("5"), max_steps=5, created_by="a"
        )
        s.add_all([run, mission])
        s.flush()
        run_id, mission_id = run.id, mission.id
    fake_litellm.queue(GOOD)

    with llm_context(run_id=run_id), llm_context(mission_id=mission_id):
        make_gateway().complete("bulk", "op", MSGS, Answer)

    [row] = _rows(llm_db)
    assert (row.run_id, row.mission_id) == (run_id, mission_id)


# ---------- embeddings ----------


def test_embed_sends_only_cache_misses(make_gateway, fake_litellm, llm_db):
    gw = make_gateway()
    dims = gw.routing.embedding.dimensions

    first = gw.embed(["alpha", "beta"])
    second = gw.embed(["beta", "gamma", "gamma"])

    assert first.model == gw.routing.embedding.model and first.dimensions == dims
    assert all(len(v) == dims for v in first.vectors + second.vectors)
    assert [c["input"] for c in fake_litellm.embed_calls] == [["alpha", "beta"], ["gamma"]]
    assert fake_litellm.embed_calls[0]["dimensions"] == dims
    assert second.vectors[0] == first.vectors[1]  # "beta" served from cache
    assert len(second.vectors) == 3
    rows = _rows(llm_db)
    assert [(r.task, r.cache_hit) for r in rows] == [
        ("embedding", False),
        ("embedding", True),
        ("embedding", False),
    ]
    assert rows[0].cost_usd > 0  # tiny, but not rounded to 0


def test_embed_dimension_mismatch(make_gateway, fake_litellm, llm_db):
    fake_litellm.queue([[0.1, 0.2]])
    with pytest.raises(LLMError, match="dims"):
        make_gateway().embed(["alpha"])
    [row] = _rows(llm_db)
    assert not row.success
    assert _cache_rows(llm_db) == []


def test_embed_empty_inputs(make_gateway, fake_litellm):
    gw = make_gateway()
    assert gw.embed([]).vectors == []
    with pytest.raises(ValueError, match="empty"):
        gw.embed(["ok", "  "])
    assert fake_litellm.embed_calls == []
