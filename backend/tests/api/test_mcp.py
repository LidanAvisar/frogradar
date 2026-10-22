"""MCP server (S16, D75): every tool / resource / prompt through an in-process MCP client, on the
API test fixtures. Shapes, source urls, the raw_text sentinel, size caps, whats_new widening, ask
with a mocked LLM and the budget guard, and the HTTP transport's bearer token."""

import asyncio
import json
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client

from app.core.config import Settings
from app.core.llm import BudgetExceeded
from app.db.enums import AskSource, FetchMethod, ReviewStatus
from app.db.models import AskConversation, RadarCandidate, Signal
from app.mcp import shape
from app.mcp.http import mount_mcp
from app.mcp.server import build_server
from app.rag.service import LLMFns
from tests.api.conftest import NOW, RAW_SENTINEL
from tests.api.test_ask import FakeAsk, all_supported, two_points
from tests.api.test_battlecards import _world
from tests.pipeline.factories import FakeEmbed, vec


def _fake_llm() -> FakeAsk:
    return FakeAsk({"ask": two_points, "verify_ask": all_supported})


@pytest.fixture
def server(db_session, build):  # noqa: ANN001, ANN201
    @contextmanager
    def sessions():  # noqa: ANN202
        yield db_session

    llm = _fake_llm()
    embed = FakeEmbed({"": vec(0.9)})
    srv = build_server(sessions=sessions, llm=lambda: LLMFns(complete=llm, embed=embed),
                       now=lambda: NOW)  # fmt: skip
    srv.fake_llm = llm  # type: ignore[attr-defined]
    return srv


def _embed_all(session) -> None:  # noqa: ANN001
    for sig in session.query(Signal).all():
        sig.embedding = vec(0.8)
    session.flush()


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


async def _call(srv, name: str, **args: Any):  # noqa: ANN001, ANN202
    async with Client(srv) as c:
        r = await c.call_tool(name, args)
    return r


def call(srv, name: str, **args: Any) -> dict[str, Any]:  # noqa: ANN001
    r = run(_call(srv, name, **args))
    assert not r.is_error, r.content
    text = json.dumps(r.structured_content)
    assert RAW_SENTINEL not in text and "Full article body" not in text
    return r.structured_content


def error(srv, name: str, **args: Any) -> str:  # noqa: ANN001
    r = run(_call(srv, name, **args))
    assert r.is_error
    return " ".join(getattr(c, "text", "") for c in r.content)


def test_tools_are_listed_with_descriptions(server):
    async def go():  # noqa: ANN202
        async with Client(server) as c:
            return (await c.list_tools()).tools

    tools = {t.name: t for t in run(go())}
    assert set(tools) == {"whats_new", "search_intel", "get_signal", "get_battlecard",
                          "pricing_changes", "radar", "ask"}  # fmt: skip
    assert "PAID" in tools["ask"].description and "budget" in tools["ask"].description
    assert "Free" in tools["whats_new"].description


def test_whats_new_ranks_and_filters_by_company(server, build):
    fw, price, _, gh = _world(build)
    out = call(server, "whats_new", days=2, min_impact=3)
    assert [i["id"] for i in out["items"]] == [fw.id, gh.id, price.id]  # impact × conf, then newest
    first = out["items"][0]
    assert first["url"].startswith("https://") and first["company"] == "Repository Firewall"
    assert first["impact"] == 4 and first["confidence"] in ("high", "medium", "low")
    assert out["window_days"] == 2 and out["note"] is None
    only = call(server, "whats_new", days=2, company="Sonatype")
    assert [i["id"] for i in only["items"]] == [fw.id, price.id]
    assert "Unknown company 'nope'" in error(server, "whats_new", company="nope")


def test_whats_new_widens_an_empty_window_and_says_so(server, build):
    build.signal("Older news", impact=4, hours_ago=24 * 4)
    out = call(server, "whats_new")  # days=1
    assert out["window_days"] == 7 and out["requested_days"] == 1
    assert out["note"] == "No new signals in the last day; showing the last 7 days."
    assert [i["title"] for i in out["items"]] == ["Older news"]
    assert out["newest_signal_at"].startswith((NOW - timedelta(days=4)).date().isoformat())
    build.signal("Ancient", impact=4, hours_ago=24 * 50)
    empty = call(server, "whats_new", min_impact=5)
    assert empty["items"] == [] and empty["window_days"] == 30
    assert empty["note"].startswith("No signals with impact ≥ 5 in the last 30 days")


def test_get_signal_has_summary_why_and_verified_quote_only(server, build):
    sig = build.signal("Big launch", quote="Exact words.")
    out = call(server, "get_signal", id=sig.id)
    assert out["summary"] == "A summary." and out["why_jfrog_cares"] == "Why it matters."
    assert out["evidence_quote"] == "Exact words." and out["url"] == sig.url
    unverified = build.signal("Shaky", quote="Not found.", quote_verified=False)
    assert call(server, "get_signal", id=unverified.id)["evidence_quote"] is None
    rejected = build.signal("Rejected", status=ReviewStatus.REJECTED)
    assert "No signal" in error(server, "get_signal", id=rejected.id)


def test_get_battlecard_sections_citations_and_matrix(server, build):
    fw, price, gone, _ = _world(build)
    out = call(server, "get_battlecard", company="sonatype")
    assert out["company"] == "Sonatype" and out["positioning"]["citations"] == [1]
    assert [s["signal_id"] for s in out["sources"]] == [fw.id, price.id]
    assert all(s["url"].startswith("https://") for s in out["sources"])
    assert [w["text"] for w in out["strengths"]] == ["Firewall 2 blocks malware."]
    assert out["weaknesses"] == [{"text": "Cloud Pro got pricier.", "citations": [2]}]
    rows = {r["capability"]: r for r in out["matrix"]}
    assert rows["Package-Curation"]["competitor"] == "full"
    assert rows["Package-Curation"]["evidence_url"] == fw.url
    assert out["capabilities_without_evidence"] == ["Sbom"]  # its evidence is no longer usable
    assert out["app_url"].endswith("/battlecards/sonatype")
    assert "Unknown company" in error(server, "get_battlecard", company="acme")


def test_pricing_changes_verified_pairs_and_window(server, build):
    page = build.source("A Pricing", method=FetchMethod.PAGE_DIFF)
    verified = [{"label": "Cloud > Pro", "before": "$1,620 / year", "after": "$1,950 / year"}]
    build.signal("Pricing page changed", source=page, hours_ago=24 * 5,
                 meta={"kind": "page_diff", "price_changes": verified})  # fmt: skip
    build.signal("Old pricing", source=page, hours_ago=24 * 200, meta={"kind": "page_diff"})
    out = call(server, "pricing_changes", days=90)
    (item,) = out["items"]
    assert item["changes"] == verified and item["summary"] is None
    assert item["url"].startswith("https://")


def test_radar_lists_top_candidates_with_relation(server, build, db_session):
    db_session.add_all([
        RadarCandidate(name="Regshield", key="regshield", kind="open_source_project",
                       first_seen=NOW, last_seen=NOW, score=60.0, status="new",
                       metrics={"relation": "threatens", "mentions": 2}, mention_signal_ids=[1, 2],
                       profile_status="ok",
                       profile={"items": {"what_it_is": {"statement": "A registry firewall [#1].",
                                                         "signal_ids": [1], "check": "partial"}}}),
        RadarCandidate(name="Low", key="low", kind="company", first_seen=NOW, last_seen=NOW,
                       score=10.0, status="new", metrics={"relation": "threatens"}),
    ])  # fmt: skip
    db_session.flush()
    out = call(server, "radar")
    (item,) = out["items"]
    assert item["name"] == "Regshield" and item["relation"] == "threatens"
    assert item["what_it_is"] == "A registry firewall." and item["app_url"].endswith(
        f"/radar/{item['id']}"
    )
    assert call(server, "radar", relation="complements")["items"] == []


def test_search_intel_returns_ids_urls_and_no_llm_call(server, build):
    fw, *_ = _world(build)
    _embed_all(build.s)
    out = call(server, "search_intel", query="firewall malware", company="sonatype")
    assert out["filters"]["company"] == "sonatype"
    ids = [h["id"] for h in out["items"]]
    assert f"sig:{fw.id}" in ids
    assert all(h["url"].startswith("http") for h in out["items"])
    assert server.fake_llm.calls == []  # embedding only, no language model


def test_ask_answers_with_sources_and_is_stored_as_mcp(server, build, db_session):
    fw, *_ = _world(build)
    _embed_all(db_session)
    out = call(server, "ask", question="What did Sonatype launch?")
    assert server.fake_llm.ops() == ["ask", "verify_ask"]
    assert out["not_enough_evidence"] is False
    assert out["sources"] and all(x["url"].startswith("http") for x in out["sources"])
    conv = db_session.query(AskConversation).order_by(AskConversation.id.desc()).first()
    assert conv.source == AskSource.MCP


def test_ask_reports_the_budget_guard(server, build, db_session):
    _world(build)
    _embed_all(db_session)
    server.fake_llm.handlers["ask"] = BudgetExceeded("daily LLM budget reached: $5.00 of $5.00")
    msg = error(server, "ask", question="What did Sonatype launch?")
    assert "budget is reached" in msg


def test_caps_on_items_and_text(server, build):
    for i in range(30):
        build.signal(f"News {i}", impact=4, hours_ago=1 + i / 10, takeaway="x" * 2000)
    out = call(server, "whats_new")
    assert len(out["items"]) == shape.MAX_ITEMS and out["truncated"] is True
    assert all(len(i["takeaway"]) <= 300 for i in out["items"])
    assert len(json.dumps(out)) <= shape.MAX_BYTES + 200


def test_resources_and_prompts(server, build):
    _world(build)

    async def go():  # noqa: ANN202
        async with Client(server) as c:
            res = (await c.list_resources()).resources
            brief = await c.read_resource("frogradar://brief/today")
            cards = await c.read_resource("frogradar://battlecards")
            prompts = (await c.list_prompts()).prompts
            p = await c.get_prompt("competitor_briefing", {"company": "sonatype"})
            return res, brief, cards, prompts, p

    res, brief, cards, prompts, p = run(go())
    assert {str(r.uri) for r in res} == {"frogradar://brief/today", "frogradar://battlecards"}
    brief_text = brief.contents[0].text
    assert RAW_SENTINEL not in brief_text and RAW_SENTINEL not in cards.contents[0].text
    data = json.loads(brief_text)
    assert data["developments"] and all(
        d["url"].startswith("https://") for d in data["developments"]
    )
    assert [i["slug"] for i in json.loads(cards.contents[0].text)["items"]][:1] == ["sonatype"]
    assert {pr.name for pr in prompts} == {"morning_brief", "competitor_briefing"}
    assert "get_battlecard('sonatype')" in p.messages[0].content.text


def test_http_transport_requires_the_token():
    app = FastAPI()
    assert mount_mcp(app, Settings(_env_file=None, mcp_token="")) is None
    assert not any(getattr(r, "path", "") == "/mcp" for r in app.router.routes)

    @asynccontextmanager
    async def lifespan(a: FastAPI):  # noqa: ANN202
        async with a.state.mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.mcp = mount_mcp(app, Settings(_env_file=None, mcp_token="s3cret"))
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"}}}  # fmt: skip
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.post("/mcp", json=body, headers=headers).status_code == 401
        wrong = {**headers, "authorization": "Bearer nope"}
        assert client.post("/mcp", json=body, headers=wrong).status_code == 401
        ok = client.post("/mcp", json=body, headers={**headers, "authorization": "Bearer s3cret"})
        assert ok.status_code == 200, ok.text
        assert "FrogRadar" in ok.text
