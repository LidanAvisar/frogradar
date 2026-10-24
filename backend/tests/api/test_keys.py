"""Keys (D80, D83): the startup check names a missing required key; key values never appear in a
log, an API response, the status output or an MCP tool result."""

import json
import logging

import pytest

from app.core import preflight
from app.core.config import get_settings
from app.core.status import format_table, get_status, log_status
from app.mcp.server import build_server
from app.rag.service import LLMFns
from tests.api.conftest import NOW
from tests.api.test_mcp import _fake_llm, call
from tests.pipeline.factories import FakeEmbed, vec

# Values that look like real keys; none of them may leave the process.
SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-LEAKCHECK-a1b2c3d4e5f6",
    "OPENAI_API_KEY": "sk-proj-LEAKCHECK-f6e5d4c3b2a1",
    "TAVILY_API_KEY": "tvly-LEAKCHECK-0011223344",
    "GITHUB_TOKEN": "ghp_LEAKCHECK5566778899",
    "MCP_TOKEN": "mcp-LEAKCHECK-aabbccddeeff",
}


@pytest.fixture
def keys(monkeypatch):
    def set_keys(**values: str) -> None:
        for name in SECRETS:  # "" wins over a developer's own .env file (pydantic reads it too)
            monkeypatch.setenv(name, "")
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield set_keys
    get_settings.cache_clear()


def _leaks(text: str) -> list[str]:
    return [name for name, value in SECRETS.items() if value in text or "LEAKCHECK" in text]


# ---------- startup check ----------


def test_preflight_names_each_missing_required_key_and_where_to_put_it(keys, capsys):
    keys()
    msg = preflight.missing_message(get_status())
    assert msg is not None
    assert "ANTHROPIC_API_KEY (for bulk" in msg and "OPENAI_API_KEY (for verify" in msg
    assert ".env" in msg and ".env.example" in msg and "make up" in msg
    assert preflight.main() == 1
    assert "can't start" in capsys.readouterr().err

    keys(ANTHROPIC_API_KEY=SECRETS["ANTHROPIC_API_KEY"])
    msg = preflight.missing_message(get_status())
    assert msg is not None and "OPENAI_API_KEY" in msg and "ANTHROPIC_API_KEY" not in msg


def test_preflight_passes_with_the_required_keys_and_optional_ones_only_limit_features(keys):
    keys(ANTHROPIC_API_KEY=SECRETS["ANTHROPIC_API_KEY"], OPENAI_API_KEY=SECRETS["OPENAI_API_KEY"])
    assert preflight.main() == 0
    st = get_status()
    assert st.missing_required() == []
    missions = st.feature("missions")
    assert missions.state == "degraded" and "TAVILY_API_KEY" in missions.reason
    assert st.feature("ask").state == "enabled"
    assert st.feature("mcp_http").state == "off"


# ---------- values never leave the process ----------


def test_key_values_never_appear_in_logs_status_or_api_responses(keys, client, build, caplog,
                                                                 capsys):  # fmt: skip
    keys(**SECRETS)
    build.signal("A signal")
    caplog.set_level(logging.DEBUG)

    log_status()
    preflight.main()
    print(format_table(get_status()))
    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["extra_keys"] == {"TAVILY_API_KEY": True, "GITHUB_TOKEN": True,
                                           "MCP_TOKEN": True}  # fmt: skip

    # Every GET route without path parameters, as the web app would call it.
    from app.main import app

    bodies = [status.text]
    for path, item in app.openapi()["paths"].items():
        if "get" in item and "{" not in path:
            r = client.get(path)
            assert r.status_code < 500, path
            bodies.append(r.text)

    out = capsys.readouterr()
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for where, text in [("api", "\n".join(bodies)), ("stdout", out.out), ("stderr", out.err),
                        ("logs", logs)]:  # fmt: skip
        assert _leaks(text) == [], where


def test_key_values_never_appear_in_mcp_tool_results(keys, db_session, build):
    keys(**SECRETS)
    build.signal("A signal")
    from contextlib import contextmanager

    @contextmanager
    def sessions():  # noqa: ANN202
        yield db_session

    fns = LLMFns(complete=_fake_llm(), embed=FakeEmbed({"": vec(0.9)}))
    srv = build_server(sessions=sessions, now=lambda: NOW, llm=lambda: fns)
    for tool, args in [
        ("whats_new", {"days": 7, "min_impact": 1}),
        ("search_intel", {"query": "package firewall"}),
    ]:
        assert _leaks(json.dumps(call(srv, tool, **args))) == [], tool
