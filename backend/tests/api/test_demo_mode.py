# ruff: noqa: F811 - pytest fixtures imported from test_ask are used as arguments
"""Demo mode and the key check (S17, D76–D80): pinned clock, /api/status, demo Ask and missions,
key pre-checks in live mode, same-family verify override, stripped signals left alone."""

from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import func, select

from app.api.ask import require_ask_keys
from app.api.missions import require_mission_keys
from app.core import catalog, clock
from app.core.config import get_settings
from app.core.status import get_status
from app.db.models import AskTurn, Signal
from app.demo.questions import demo_questions, match_demo_question
from app.main import app
from app.pipeline.scope import in_scope, not_stripped
from tests.api.test_ask import Q, llm, world  # noqa: F401 - fixtures

pytestmark = pytest.mark.db

KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY", "GITHUB_TOKEN", "MCP_TOKEN")


@pytest.fixture
def env(monkeypatch):
    """Set env vars and rebuild settings / routing for the test."""

    def _set(**values: str) -> None:
        for k, v in values.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        catalog.clear_cache()

    for k in KEYS:
        monkeypatch.setenv(k, "")
    _set()
    yield _set
    get_settings.cache_clear()
    catalog.clear_cache()


@pytest.fixture
def demo(env):
    env(DEMO_MODE="true", DEMO_AS_OF="2026-10-21T17:33:00Z")


# ---------- clock ----------


def test_clock_is_the_wall_clock_in_live_mode(env):
    assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5


def test_clock_is_pinned_to_the_snapshot_in_demo_mode(demo):
    assert clock.now() == datetime(2026, 10, 21, 17, 33, tzinfo=UTC)


def test_pin_now_wins_in_any_mode(env):
    env(PIN_NOW="2026-01-02T03:04:05")
    assert clock.now() == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


# ---------- status / key check ----------


def test_no_keys_turns_features_off_with_reasons(env):
    st = get_status()
    assert st.mode == "live" and not any(m.key_present for m in st.models)
    assert st.feature("ask").state == "off" and "OPENAI_API_KEY" in st.feature("ask").reason
    assert st.feature("pipeline").state == "off"
    assert st.feature("analyst").state == "enabled"


def test_keys_enable_features_and_tavily_only_degrades_missions(env):
    env(ANTHROPIC_API_KEY="x", OPENAI_API_KEY="x", GITHUB_TOKEN="x")
    st = get_status()
    assert st.feature("ask").state == "enabled"
    assert st.feature("pipeline").state == "enabled"
    assert st.feature("missions").state == "degraded" and "TAVILY" in st.feature("missions").reason


def test_status_endpoint_never_returns_key_values(client, env):
    env(ANTHROPIC_API_KEY="sk-secret-value")
    r = client.get("/api/status")
    assert r.status_code == 200
    assert "sk-secret-value" not in r.text
    d = r.json()
    assert d["mode"] == "live" and d["demo_questions"] == []
    assert d["cost_estimates_usd"]["ask"] > 0 and d["daily_budget_usd"] > 0


def test_live_ask_without_keys_answers_503_with_the_reason(client, env):
    app.dependency_overrides.pop(require_ask_keys, None)
    r = client.post("/api/ask", json={"question": "Anything new?"})
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"]


def test_live_mission_without_keys_answers_503(client, env):
    app.dependency_overrides.pop(require_mission_keys, None)
    r = client.post("/api/missions", json={"brief": "Look into Acme's new registry features."})
    assert r.status_code == 503 and "needs" in r.json()["detail"]


# ---------- same-family verify ----------


def _same_family(config_copy):
    path = config_copy / "llm_routing.yaml"
    data = yaml.safe_load(path.read_text())
    data["tasks"]["verify"]["model"] = "anthropic/claude-haiku-5-5"
    path.write_text(yaml.safe_dump(data))
    return config_copy


def test_same_family_verify_is_refused_by_default(env, config_copy):
    with pytest.raises(catalog.ConfigError, match="ALLOW_SAME_FAMILY_VERIFY"):
        catalog.load_llm_routing(_same_family(config_copy))


def test_same_family_verify_starts_with_a_warning_when_allowed(env, config_copy, monkeypatch):
    env(ALLOW_SAME_FAMILY_VERIFY="true", CONFIG_DIR=str(_same_family(config_copy)))
    routing = catalog.load_llm_routing()
    assert routing.same_family_verify
    assert any("weaker" in w for w in get_status().warnings)


# ---------- demo mode ----------


def test_demo_questions_match_loosely_and_return_the_exact_text():
    first = demo_questions()[0]
    assert match_demo_question("  " + first.lower().rstrip("?") + " ") == first
    assert match_demo_question("Something else entirely?") is None


def test_demo_status_lists_the_questions_and_as_of(client, demo):
    d = client.get("/api/status").json()
    assert d["mode"] == "demo" and d["as_of"].startswith("2026-10-21T17:33")
    assert d["demo_questions"] == demo_questions()


def test_demo_ask_refuses_other_questions_without_writing(client, demo, db_session):
    r = client.post("/api/ask", json={"question": "What is the weather today?"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["demo"] is True and detail["suggestions"] == demo_questions()
    assert db_session.scalar(select(func.count()).select_from(AskTurn)) == 0


def test_demo_ask_answers_a_curated_question_as_a_new_conversation(client, demo, world, llm):
    assert Q in demo_questions()
    first = client.post("/api/ask", json={"question": Q.lower()})
    assert first.status_code == 200, first.text
    again = client.post(
        "/api/ask", json={"question": Q, "conversation_id": first.json()["conversation_id"]}
    )
    assert again.status_code == 200
    assert again.json()["conversation_id"] != first.json()["conversation_id"]
    assert "ask_rewrite" not in llm.ops()  # no history → the cached prompt


def test_demo_mission_create_is_refused(client, demo):
    r = client.post("/api/missions", json={"brief": "Look into Acme's new registry features."})
    assert r.status_code == 409 and "Demo mode" in r.json()["detail"]["message"]


# ---------- stripped signals ----------


def test_stripped_signals_are_left_out_of_pipeline_work(build, client, db_session):
    a = build.signal("Kept", meta={"snapshot_stripped": True})
    b = build.signal("Processed")
    ids = set(db_session.scalars(select(Signal.id).where(in_scope(), not_stripped())))
    assert b.id in ids and a.id not in ids


# ---------- pipeline preflight ----------


def test_pipeline_refuses_demo_mode_and_a_missing_bulk_key(env):
    from app.pipeline.graph import NotRunnable, key_skips

    with pytest.raises(NotRunnable, match="ANTHROPIC_API_KEY"):
        key_skips()
    env(DEMO_MODE="true")
    with pytest.raises(NotRunnable, match="demo mode"):
        key_skips()


def test_pipeline_skips_embedding_and_verify_nodes_without_openai(env):
    from app.pipeline.graph import key_skips

    env(ANTHROPIC_API_KEY="x")
    assert key_skips() == {"embed", "dedup", "verify", "radar"}
    env(OPENAI_API_KEY="x")
    assert key_skips() == frozenset()


def test_recent_questions_list_a_repeated_question_once(client, world, llm):
    first = client.post("/api/ask", json={"question": Q}).json()["conversation_id"]
    second = client.post("/api/ask", json={"question": Q}).json()["conversation_id"]
    listed = client.get("/api/ask/conversations").json()
    assert [c["id"] for c in listed] == [second] and second != first
