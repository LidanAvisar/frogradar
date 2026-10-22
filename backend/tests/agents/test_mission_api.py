"""Missions API + findings → intelligence: shapes, prefill, plan approval, stop, approve / reject,
radar profile refresh, and the rule that fetched page text is never returned."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.mission.runner import MissionRunner, Tools
from app.agents.mission.service import approve_plan
from app.agents.mission.worker import get_worker
from app.api.ask import get_llm
from app.api.deps import get_now
from app.api.missions import require_mission_keys
from app.db.enums import (
    FeedbackTarget,
    MissionOrigin,
    MissionSourceKind,
    MissionStatus,
    ReviewStatus,
    SignalCategory,
)
from app.db.models import (
    AskConversation,
    AskTurn,
    Feedback,
    Mission,
    MissionFinding,
    MissionSource,
    RadarCandidate,
    Signal,
    signal_capabilities,
    signal_entities,
)
from app.db.session import get_session
from app.main import app
from app.pipeline.scope import usable
from app.rag.service import LLMFns
from tests.agents.conftest import NOW, RAW_SENTINEL, ScriptedLLM, make_candidate, make_mission
from tests.pipeline.factories import FakeEmbed, ensure_capability, entity


class FakeWorker:
    def __init__(self) -> None:
        self.jobs: list[tuple[int, str]] = []

    def submit(self, mission_id: int, phase: str) -> None:
        self.jobs.append((mission_id, phase))


@pytest.fixture
def worker() -> FakeWorker:
    return FakeWorker()


@pytest.fixture
def client(s: Session, worker: FakeWorker, llm: ScriptedLLM) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: s
    app.dependency_overrides[get_now] = lambda: NOW
    app.dependency_overrides[get_worker] = lambda: worker
    app.dependency_overrides[require_mission_keys] = lambda: None  # no provider keys in tests
    app.dependency_overrides[get_llm] = lambda: LLMFns(complete=llm, embed=FakeEmbed())
    yield TestClient(app)
    app.dependency_overrides.clear()


def _finished(s: Session, cfg, tools: Tools, **kw: Any) -> Mission:  # noqa: ANN001
    m = make_mission(s, cfg, **kw)
    MissionRunner(s, m.id, tools, cfg).plan()
    approve_plan(s, m, None, "Dana")
    s.commit()
    MissionRunner(s, m.id, tools, cfg).research()
    s.expire_all()
    return s.get(Mission, m.id)


def _walk(obj: Any) -> str:
    return str(obj)


def test_create_list_detail_shapes_and_worker_jobs(client, worker, s):
    sonatype = entity(s, "sonatype", "Sonatype")
    s.commit()
    r = client.post("/api/missions", json={"brief": "Does Sonatype offer reachability analysis?",
                                           "scope": {"company_ids": [sonatype.id], "days": 180},
                                           "analyst": "Dana"})  # fmt: skip
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "queued" and d["budget_usd"] == 0.30 and d["max_steps"] == 12
    assert d["scope_label"] == "Sonatype · last 180 days" and d["created_by"] == "Dana"
    assert d["plan"] is None and d["steps"] == [] and d["findings"] == []
    assert worker.jobs == [(d["id"], "plan")]
    lst = client.get("/api/missions").json()["items"]
    assert [m["id"] for m in lst] == [d["id"]] and lst[0]["origin_label"] == "New mission"
    assert client.get("/api/missions/999").status_code == 404
    bad = client.post("/api/missions", json={"brief": "Short but ok?", "budget_usd": 50})
    assert bad.status_code == 422


def test_plan_approval_and_edit_via_api(client, worker, s, cfg, tools):
    m = make_mission(s, cfg)
    MissionRunner(s, m.id, tools, cfg).plan()
    d = client.get(f"/api/missions/{m.id}").json()
    assert d["status"] == "awaiting_approval" and len(d["plan"]["sub_questions"]) == 3
    edited = [{"question": "Is reachability in Lifecycle?", "search_query": "lifecycle reach"}]
    r = client.post(f"/api/missions/{m.id}/plan", json={"analyst": "Dana", "sub_questions": edited})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "queued" and d["plan"]["approved"] and d["plan"]["edited_by_analyst"]
    assert d["plan"]["approved_by"] == "Dana" and worker.jobs == [(m.id, "research")]
    assert client.post(f"/api/missions/{m.id}/plan", json={}).status_code == 409


def test_stop_queued_cancels_and_running_requests_stop(client, s, cfg):
    a = make_mission(s, cfg)
    b = make_mission(s, cfg)
    b.status = MissionStatus.RUNNING
    s.commit()
    assert client.post(f"/api/missions/{a.id}/stop").json()["status"] == "cancelled"
    d = client.post(f"/api/missions/{b.id}/stop").json()
    assert d["status"] == "running" and d["stop_requested"] is True
    assert client.post(f"/api/missions/{a.id}/stop").status_code == 409


def test_detail_never_returns_page_text_and_labels_verification(client, s, cfg, tools, llm):
    import re

    def verdicts(call):
        keys = re.findall(r"Finding (F\d+):", call.messages[-1]["content"])
        return {
            "items": [{"key": k, "verdict": "partial", "note": "Date not stated."} for k in keys]
        }

    llm.handlers["mission_verify"] = verdicts
    m = _finished(s, cfg, tools, max_steps=4)
    body = client.get(f"/api/missions/{m.id}").text
    listing = client.get("/api/missions").text
    assert RAW_SENTINEL not in body and RAW_SENTINEL not in listing
    assert "excerpt" not in body and "raw_text" not in body
    d = client.get(f"/api/missions/{m.id}").json()
    (f,) = d["findings"]
    assert f["verification"] == "partial" and f["verification_label"] == "Partly verified"
    assert f["confidence"] == "medium"  # extracted as high, capped because only partly verified
    assert f["source"]["url"].startswith("https://") and f["evidence_quote"]
    assert d["steps_used"] == 4 and d["status"] == "completed"
    assert d["report"]["generated"] == "llm"
    assert [s_["counted"] for s_ in d["steps"]] == [False, True, True, True, True, False]


def test_approved_finding_becomes_a_usable_signal_linked_to_company_and_capability(
    client, s, cfg, tools
):
    sonatype = entity(s, "sonatype", "Sonatype")
    cap = ensure_capability(s, "reachability-analysis")
    s.commit()
    m = _finished(s, cfg, tools, max_steps=4, origin_kind=MissionOrigin.CAPABILITY,
                  origin_ref={"entity_id": sonatype.id, "capability_id": cap},
                  company_ids=[sonatype.id])  # fmt: skip
    (f,) = s.scalars(select(MissionFinding).where(MissionFinding.mission_id == m.id)).all()
    r = client.post(f"/api/missions/findings/{f.id}/approve", json={"analyst": "Dana"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert RAW_SENTINEL not in r.text
    sig = s.get(Signal, out["signal_id"])
    assert sig.mission_id == m.id and sig.source_id is None
    assert sig.url == "https://www.sonatype.com/blog/reachability"
    assert sig.status == ReviewStatus.APPROVED and sig.verification == "supported"
    assert sig.evidence_quote == f.evidence_quote and sig.evidence_quote_verified
    assert (
        sig.category == SignalCategory.FEATURE_UPDATE
        and sig.why_jfrog_cares == "Competes with Xray."
    )
    assert RAW_SENTINEL in sig.raw_text  # stored for analysis, never returned
    assert sig.embedding is not None
    links = s.execute(select(signal_entities).where(signal_entities.c.signal_id == sig.id)).all()
    assert [(link.entity_id, link.is_primary) for link in links] == [(sonatype.id, True)]
    caps = (
        s.execute(
            select(signal_capabilities.c.capability_id).where(
                signal_capabilities.c.signal_id == sig.id
            )
        )
        .scalars()
        .all()
    )
    assert caps == [cap]
    assert s.scalar(select(Signal.id).where(usable(), Signal.id == sig.id)) == sig.id
    fb = s.scalars(
        select(Feedback).where(Feedback.target_type == FeedbackTarget.MISSION_FINDING)
    ).all()
    assert [(x.action.value, x.analyst) for x in fb] == [("approve", "Dana")]
    # The signal shows up in the read API (Brief / Signals use the same visible() rules).
    sig_api = client.get(f"/api/signals/{sig.id}")
    assert sig_api.status_code == 200 and RAW_SENTINEL not in sig_api.text
    # Approving twice is refused; rejecting afterwards takes the signal out again.
    assert client.post(f"/api/missions/findings/{f.id}/approve", json={}).status_code == 409
    r = client.post(
        f"/api/missions/findings/{f.id}/reject", json={"analyst": "Dana", "reason": "outdated"}
    )
    assert r.status_code == 200 and r.json()["finding"]["status"] == "rejected"
    s.refresh(sig)
    assert sig.status == ReviewStatus.REJECTED


def test_kb_finding_links_the_existing_signal_instead_of_duplicating(client, s, cfg, tools):
    m = make_mission(s, cfg)
    existing = Signal(url="https://x.test/1", canonical_url="https://x.test/1", content_hash="a" * 64,  # noqa: E501
                      title="Old signal", raw_text="text")  # fmt: skip
    s.add(existing)
    s.flush()
    src = MissionSource(mission_id=m.id, kind=MissionSourceKind.KB, url=existing.url,
                        canonical_url=f"frogradar:signal:{existing.id}", title="Old signal",
                        excerpt="summary", signal_id=existing.id)  # fmt: skip
    s.add(src)
    s.flush()
    f = MissionFinding(mission_id=m.id, sub_question_no=1, statement="A statement long enough.",
                       evidence_quote="summary quote", source_id=src.id, confidence="high",
                       verification="supported")  # fmt: skip
    s.add(f)
    s.commit()
    n = s.scalar(select(Signal.id).order_by(Signal.id.desc()))
    r = client.post(f"/api/missions/findings/{f.id}/approve", json={})
    assert r.json()["signal_id"] == existing.id
    assert s.scalar(select(Signal.id).order_by(Signal.id.desc())) == n


def test_radar_mission_adds_mentions_and_refreshes_the_profile(client, s, cfg, tools, monkeypatch):
    cand = make_candidate(s)
    s.commit()
    m = _finished(s, cfg, tools, max_steps=4, origin_kind=MissionOrigin.RADAR_CANDIDATE,
                  origin_ref={"candidate_id": cand.id})  # fmt: skip
    assert m.radar_candidate_id == cand.id and m.scope["company_names"] == ["GitGuardian"]
    (f,) = s.scalars(select(MissionFinding).where(MissionFinding.mission_id == m.id)).all()
    sig_id = client.post(f"/api/missions/findings/{f.id}/approve", json={}).json()["signal_id"]
    s.refresh(cand)
    assert sig_id in cand.mention_signal_ids

    seen = {}

    def fake_build(session, c, cfg_, complete, now, *, model=None):  # noqa: ANN001, ANN202
        seen["mentions"] = list(c.mention_signal_ids)
        c.profile = {"items": {"what_it_is": {}, "jfrog_relation": {"relation": "threatens"}},
                     "dropped": []}  # fmt: skip
        c.profile_status = "ok"
        return "ok"

    monkeypatch.setattr("app.pipeline.radar.profile.build_profile", fake_build)
    r = client.post(f"/api/missions/{m.id}/refresh-radar")
    assert r.status_code == 200, r.text
    out = r.json()
    assert seen["mentions"] == [sig_id]
    assert out["before"]["profile_status"] == "none" and out["after"]["profile_status"] == "ok"
    assert out["after"]["mentions"] == 1 and out["after"]["relation"] == "threatens"
    other = make_mission(s, cfg)
    assert client.post(f"/api/missions/{other.id}/refresh-radar").status_code == 409


def test_prefill_from_every_origin(client, s):
    sonatype = entity(s, "sonatype", "Sonatype")
    cap = ensure_capability(s, "reachability-analysis")
    cand = make_candidate(s, "StepSecurity")
    sig = Signal(url="https://x.test/9", canonical_url="https://x.test/9", content_hash="b" * 64,
                 title="Sonatype ships a thing", raw_text="t")  # fmt: skip
    s.add(sig)
    s.flush()
    s.execute(
        signal_entities.insert().values(signal_id=sig.id, entity_id=sonatype.id, is_primary=True)
    )
    conv = AskConversation(title="q")
    s.add(conv)
    s.flush()
    turn = AskTurn(conversation_id=conv.id, position=1, question="Does Snyk sell a registry?",
                   standalone_question="Does Snyk sell a registry?", answer={"gap": "No sources on Snyk registries."},  # noqa: E501
                   not_enough_evidence=True)  # fmt: skip
    s.add(turn)
    s.commit()
    p = client.get(f"/api/missions/prefill?candidate={cand.id}").json()
    assert (
        p["brief"] == "What does StepSecurity sell, and how does it overlap with JFrog's products?"
    )
    assert p["origin_kind"] == "radar_candidate" and p["origin_ref"] == {"candidate_id": cand.id}
    assert p["budget_usd"] == 0.3 and p["max_steps"] == 12
    p = client.get(f"/api/missions/prefill?entity={sonatype.id}&capability={cap}").json()
    assert p["brief"] == "Does Sonatype offer reachability-analysis?" and p["company_ids"] == [
        sonatype.id
    ]
    by_slug = client.get(
        "/api/missions/prefill?entity_slug=sonatype&capability_slug=reachability-analysis"
    ).json()
    assert by_slug["origin_ref"] == {"entity_id": sonatype.id, "capability_id": cap}
    p = client.get(f"/api/missions/prefill?signal={sig.id}").json()
    assert (
        p["origin_kind"] == "signal"
        and p["company_ids"] == [sonatype.id]
        and "Sonatype ships" in p["brief"]
    )
    p = client.get(f"/api/missions/prefill?turn={turn.id}").json()
    assert p["origin_kind"] == "ask_turn" and "No sources on Snyk registries." in p["brief"]
    assert client.get("/api/missions/prefill?candidate=999").status_code == 404
    # Creating from a prefill stores the origin.
    body = {"brief": p["brief"], "origin_kind": p["origin_kind"], "origin_ref": p["origin_ref"]}
    d = client.post("/api/missions", json=body).json()
    assert d["origin_kind"] == "ask_turn" and d["origin_label"].startswith("Ask: Does Snyk")
    assert s.get(RadarCandidate, cand.id) is not None
