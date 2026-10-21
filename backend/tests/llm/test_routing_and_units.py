"""Unit tests without a database: routing, structured-output parsing, cache keys, prompts."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.core import catalog
from app.core.catalog import load_llm_routing
from app.core.config import get_settings
from app.core.llm import cache, structured
from app.core.llm.context import current_context, llm_context
from app.core.llm.errors import scrub
from app.core.llm.prompts import PromptError, load_prompt
from app.core.llm.routing import resolve


class Answer(BaseModel):
    answer: str
    score: int


# ---------- routing ----------


def test_tiers_resolve_to_configured_models():
    routing = load_llm_routing()
    assert resolve("bulk", routing).model == routing.tasks.bulk.model
    assert resolve("synthesis", routing).model == routing.tasks.synthesis.model
    assert resolve("verify", routing).model == routing.tasks.verify.model


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_BULK", "anthropic/claude-sonnet-5-5")
    get_settings.cache_clear()
    catalog.clear_cache()
    try:
        assert resolve("bulk", load_llm_routing()).model == "anthropic/claude-sonnet-5-5"
    finally:
        monkeypatch.delenv("LLM_MODEL_BULK")
        get_settings.cache_clear()
        catalog.clear_cache()


def test_unknown_task():
    with pytest.raises(ValueError, match="unknown LLM task"):
        resolve("cheap", load_llm_routing())


# ---------- structured output ----------


@pytest.mark.parametrize(
    "text",
    [
        '{"answer": "x", "score": 1}',
        '```json\n{"answer": "x", "score": 1}\n```',
        'Sure! Here it is: {"answer": "x", "score": 1} Hope this helps.',
    ],
)
def test_parse_extracts_json(text):
    obj, err = structured.parse(text, Answer)
    assert err is None and obj == Answer(answer="x", score=1)


def test_parse_reports_field_errors():
    obj, err = structured.parse('{"answer": "x", "score": "high"}', Answer)
    assert obj is None and "score" in err


def test_schema_instruction_merges_into_system_message():
    msgs = structured.with_schema_instruction(
        [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}], Answer
    )
    assert len(msgs) == 2
    assert msgs[0]["content"].startswith("Be brief.") and '"score"' in msgs[0]["content"]


# ---------- cache keys ----------


def _key(**overrides):
    args = {
        "model": "anthropic/claude-haiku-5-5",
        "max_tokens": 100,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": "hi"}],
        "schema": Answer.model_json_schema(),
    }
    return cache.completion_key(**{**args, **overrides})


def test_cache_key_is_stable_and_sensitive_to_inputs():
    assert _key() == _key()
    assert len(_key()) == 64
    assert _key(model="anthropic/claude-sonnet-5-5") != _key()
    assert _key(temperature=0.2) != _key()
    assert _key(messages=[{"role": "user", "content": "hi!"}]) != _key()

    class Other(BaseModel):
        answer: str

    assert _key(schema=Other.model_json_schema()) != _key()


def test_cache_version_invalidates_all_keys(monkeypatch):
    before_c = _key()
    before_e = cache.embedding_key(model="openai/x", dimensions=8, text="hello")
    monkeypatch.setattr(cache, "CACHE_VERSION", cache.CACHE_VERSION + 1)
    assert _key() != before_c
    assert cache.embedding_key(model="openai/x", dimensions=8, text="hello") != before_e


# ---------- context ----------


def test_llm_context_nests_and_resets():
    assert current_context().run_id is None
    with llm_context(run_id=1):
        with llm_context(mission_id=2):
            assert (current_context().run_id, current_context().mission_id) == (1, 2)
        assert current_context().mission_id is None
    assert current_context().run_id is None


# ---------- scrubbing ----------


def test_scrub_removes_keys():
    out = scrub("bad key sk-proj-abcdef1234567890 Authorization: Bearer abc.def x-api-key: zzz")
    assert "abcdef1234567890" not in out and "abc.def" not in out and "zzz" not in out


# ---------- prompts ----------


def _write(d: Path, name: str, body: str) -> None:
    (d / name).write_text(body, encoding="utf-8")


def test_prompt_latest_and_pinned_versions(tmp_path):
    _write(tmp_path, "enrich.v1.md", "## user\nold $x")
    _write(
        tmp_path,
        "enrich.v2.md",
        "---\ndescription: new\n---\n## system\nsys\n## user\nnew $x {json}",
    )
    _write(tmp_path, "enrich_other.v9.md", "## user\nnot me")
    latest = load_prompt("enrich", prompts_dir=tmp_path)
    assert latest.id == "enrich.v2" and latest.description == "new"
    assert latest.messages(x="1") == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "new 1 {json}"},
    ]
    assert load_prompt("enrich", 1, prompts_dir=tmp_path).messages(x="1") == [
        {"role": "user", "content": "old 1"}
    ]


def test_prompt_errors(tmp_path):
    _write(tmp_path, "p.v1.md", "## user\nhello $name")
    with pytest.raises(PromptError, match="missing variable 'name'"):
        load_prompt("p", prompts_dir=tmp_path).messages()
    with pytest.raises(PromptError, match="not found"):
        load_prompt("p", 3, prompts_dir=tmp_path)
    with pytest.raises(PromptError, match="no prompt files"):
        load_prompt("nope", prompts_dir=tmp_path)
    _write(tmp_path, "q.v1.md", "## system\nonly system")
    with pytest.raises(PromptError, match="missing '## user'"):
        load_prompt("q", prompts_dir=tmp_path)


def test_shipped_smoke_prompt_loads():
    prompt = load_prompt("smoke")
    assert prompt.id.startswith("smoke.v")
    assert "bulk" in prompt.messages(tier="bulk")[-1]["content"]


# ---------- the test-suite guard itself ----------


def test_provider_keys_are_absent_in_normal_tests():
    import os

    from tests.conftest import PROVIDER_KEYS

    assert not [k for k in PROVIDER_KEYS if os.environ.get(k)]


def test_real_litellm_call_fails_the_test():
    import litellm
    from _pytest.outcomes import Failed

    with pytest.raises(Failed, match="real provider call"):
        litellm.completion(model="openai/gpt-5.4-mini", messages=[])
    with pytest.raises(Failed, match="real provider call"):
        litellm.embedding(model="openai/text-embedding-3-small", input=["x"])


# ---------- thinking setting ----------


def test_thinking_in_cache_key_only_when_set():
    old_format = cache._hash(
        {
            "kind": "completion",
            "model": "anthropic/claude-haiku-5-5",
            "params": {"max_tokens": 100, "temperature": 0.0},
            "messages": [{"role": "user", "content": "hi"}],
            "schema": Answer.model_json_schema(),
        }
    )
    assert _key() == _key(thinking="default") == old_format  # earlier cache entries stay valid
    assert _key(thinking="off") != _key() != _key(thinking="low") != _key(thinking="off")


def test_thinking_params_per_provider():
    from app.core.llm.provider import thinking_params

    assert thinking_params("anthropic/claude-haiku-5-5", "default") == {}
    # Anthropic "off" must be explicit: reasoning_effort="none" sends nothing to Anthropic.
    assert thinking_params("anthropic/claude-haiku-5-5", "off") == {
        "thinking": {"type": "disabled"}
    }
    assert thinking_params("openai/gpt-5.4-mini", "off") == {"reasoning_effort": "none"}
    assert thinking_params("openai/gpt-5.4-mini", "low") == {"reasoning_effort": "low"}
    assert thinking_params("anthropic/claude-haiku-5-5", "high") == {"reasoning_effort": "high"}


def test_thinking_config_values():
    data = load_llm_routing().model_dump()
    data["tasks"]["bulk"]["thinking"] = False  # what YAML makes of a bare `off`
    assert catalog.LLMRoutingFile.model_validate(data).tasks.bulk.thinking == "off"
    data["tasks"]["bulk"]["thinking"] = "maybe"
    with pytest.raises(ValueError):
        catalog.LLMRoutingFile.model_validate(data)
    routing = load_llm_routing()
    assert routing.tasks.bulk.thinking == "off" and routing.tasks.verify.thinking == "low"
