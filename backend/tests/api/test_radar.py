"""Radar API (S11): list / detail (no raw_text), watch / approve / dismiss, Brief teaser."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.enums import (
    DiscoveredBy,
    EntityReviewStatus,
    FeedbackAction,
    FeedbackTarget,
    FetchMethod,
    ProfileStatus,
    RadarKind,
    RadarStatus,
)
from app.db.models import Entity, Feedback, RadarCandidate, Source
from tests.api.conftest import NOW, RAW_SENTINEL

pytestmark = pytest.mark.db


def _candidate(
    s,
    name="Regshield",
    *,
    score=50.0,
    status=RadarStatus.NEW,
    mentions=(),
    sources=(),
    profile=None,
    kind=RadarKind.OPEN_SOURCE_PROJECT,
    **kw,
):
    c = RadarCandidate(
        name=name,
        key=name.casefold().replace(" ", ""),
        kind=kind,
        first_seen=NOW - timedelta(days=5),
        last_seen=NOW - timedelta(days=1),
        mention_signal_ids=list(mentions),
        source_ids=list(sources),
        segments=["supply_chain_security"],
        status=status,
        score=score,
        score_breakdown={
            "formula": "radar_score.v1",
            "score": score,
            "components": {
                "mentions": {"value": {"mentions": 2}, "factor": 0.4, "weight": 0.2, "points": 8.0}
            },
        },
        metrics={
            "mentions": len(mentions),
            "sources": len(sources),
            "stars": 800,
            "star_velocity": 40.0,
            "star_velocity_basis": "age",
        },
        meta={
            "mention_origins": {str(m): "hn_new_entity" for m in mentions},
            "contexts": {"1": "A registry firewall."},
            "other_names": ["RegShield Proxy"],
        },
        profile=profile,
        profile_status=ProfileStatus.OK if profile else ProfileStatus.NONE,
        merges=[],
        **kw,
    )
    s.add(c)
    s.flush()
    return c


def _profile(ids):
    return {
        "generated_at": NOW.isoformat(),
        "items": {
            "what_it_is": {
                "key": "what_it_is",
                "statement": "An open-source registry firewall.",
                "signal_ids": ids[:1],
            },
            "jfrog_relation": {
                "key": "jfrog_relation",
                "statement": "Overlaps with Curation.",
                "signal_ids": ids[:1],
                "relation": "threatens",
                "products": ["jfrog-curation"],
            },
            "why_notable_now": {
                "key": "why_notable_now",
                "statement": "Launched on HN this week.",
                "signal_ids": ids,
            },
        },
        "dropped": [{"key": "stage", "reason": "verifier_partial"}],
    }


@pytest.fixture
def radar(build, db_session):
    hn = build.source("Hacker News (discovery)", method=FetchMethod.HN_SEARCH)
    a = build.signal("Show HN: Regshield", source=hn, meta={"kind": "discovery", "discovery": "hn"})
    b = build.signal("Regshield 1.0", source=hn, meta={"kind": "discovery", "discovery": "hn"})
    jfrog = build.entity("jfrog", "JFrog", is_self=True)
    build.entity("jfrog-curation", "JFrog Curation", parent=jfrog)
    top = _candidate(
        db_session,
        "Regshield",
        score=61.5,
        mentions=[a.id, b.id],
        sources=[hn.id],
        profile=_profile([a.id, b.id]),
        repo_url="https://github.com/regshield/regshield",
        homepage="https://regshield.io",
    )
    watching = _candidate(db_session, "Pkgguard", score=40.0, status=RadarStatus.WATCHING)
    low = _candidate(db_session, "Tinytool", score=12.0, kind=RadarKind.COMPANY)
    dismissed = _candidate(db_session, "Gamey", score=90.0, status=RadarStatus.DISMISSED)
    merged = _candidate(db_session, "Regshield Proxy", score=70.0)
    merged.merged_into_id = top.id
    db_session.flush()
    return {
        "top": top,
        "watching": watching,
        "low": low,
        "dismissed": dismissed,
        "merged": merged,
        "signals": (a, b),
    }


def test_list_sorted_by_score_open_only(client, radar):
    data = client.get("/api/radar").json()
    assert [c["name"] for c in data["items"]] == ["Regshield", "Pkgguard", "Tinytool"]
    assert data["total"] == 3
    top = data["items"][0]
    assert top["breakdown"]["components"]["mentions"]["points"] == 8.0
    assert top["line"] == "Launched on HN this week." and top["line_source"] == "why_notable_now"
    assert top["sources_count"] == 1 and top["mentions_count"] == 2
    assert top["metrics"]["star_velocity_basis"] == "age"
    assert top["origins"] == ["hn_new_entity"]
    assert data["items"][2]["line_source"] == "context"
    dismissed = client.get("/api/radar?status=dismissed").json()["items"]
    assert [c["name"] for c in dismissed] == ["Gamey"]
    assert client.get("/api/radar?status=bogus").status_code == 422


def test_detail_has_cited_statements_and_no_raw_text(client, radar):
    top = radar["top"]
    r = client.get(f"/api/radar/{top.id}")
    assert r.status_code == 200
    assert RAW_SENTINEL not in r.text and "Full article body" not in r.text
    d = r.json()
    assert [s["key"] for s in d["statements"]] == [
        "what_it_is",
        "jfrog_relation",
        "why_notable_now",
    ]
    rel = d["statements"][1]
    assert rel["products"] == ["JFrog Curation"] and rel["relation"] == "threatens"
    assert rel["citations"][0]["title"] == "Show HN: Regshield"
    assert d["dropped_statements"] == 1
    assert [m["title"] for m in d["mentions"]] == ["Show HN: Regshield", "Regshield 1.0"]
    assert d["mentions"][0]["source"] == "Hacker News (discovery)"
    assert d["other_names"] == ["RegShield Proxy"]
    assert client.get(f"/api/radar/{radar['merged'].id}").status_code == 404
    assert client.get("/api/radar/999999").status_code == 404


def test_watch_then_approve_creates_entity_source_and_feedback(client, radar, db_session):
    top = radar["top"]
    r = client.post(f"/api/radar/{top.id}/watch", json={"comment": "keep an eye"})
    assert r.status_code == 200 and r.json()["status"] == "watching"
    assert client.post(f"/api/radar/{top.id}/watch").status_code == 409

    r = client.post(f"/api/radar/{top.id}/approve", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["candidate"]["status"] == "approved"
    assert out["entity"] == {
        "slug": "regshield",
        "name": "Regshield",
        "type": "product",
        "relationship": "competitor",
    }
    assert out["sources"] == [
        {
            "name": "Regshield releases (GitHub)",
            "url": "https://github.com/regshield/regshield",
            "is_active": False,
        }
    ]
    e = db_session.scalar(select(Entity).where(Entity.slug == "regshield"))
    assert e.discovered_by == DiscoveredBy.RADAR and e.review_status == EntityReviewStatus.APPROVED
    assert e.aliases == ["RegShield Proxy"] and e.website == "https://regshield.io"
    assert e.description == "An open-source registry firewall."
    src = db_session.scalar(select(Source).where(Source.entity_id == e.id))
    assert (
        src.options == {"repos": ["regshield/regshield"]}
        and src.discovered_by == DiscoveredBy.RADAR
    )
    actions = (
        db_session.execute(
            select(Feedback.action).where(
                Feedback.target_type == FeedbackTarget.RADAR_CANDIDATE, Feedback.target_id == top.id
            )
        )
        .scalars()
        .all()
    )
    assert sorted(actions) == sorted([FeedbackAction.WATCH, FeedbackAction.APPROVE])
    # Decided: no second decision.
    assert client.post(f"/api/radar/{top.id}/approve").status_code == 409
    assert client.post(f"/api/radar/{top.id}/dismiss", json={"reason": "other"}).status_code == 409
    assert out["candidate"]["approved_entity"] == "regshield"


def test_approve_options_and_slug_collision(client, radar, build):
    build.entity("pkgguard", "Something Else")
    w = radar["watching"]
    r = client.post(
        f"/api/radar/{w.id}/approve",
        json={"relationship": "partner", "type": "company", "add_sources": False},
    )
    assert r.status_code == 200
    assert r.json()["entity"] == {
        "slug": "pkgguard-2",
        "name": "Pkgguard",
        "type": "company",
        "relationship": "partner",
    }
    assert r.json()["sources"] == []


def test_dismiss_records_reason(client, radar, db_session):
    low = radar["low"]
    assert client.post(f"/api/radar/{low.id}/dismiss", json={"reason": "bogus"}).status_code == 422
    r = client.post(f"/api/radar/{low.id}/dismiss", json={"reason": "too_small", "comment": "tiny"})
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed" and r.json()["dismiss_reason"] == "too_small"
    fb = db_session.scalar(select(Feedback).where(Feedback.target_id == low.id))
    assert (fb.action, fb.reason_code, fb.comment) == (FeedbackAction.REJECT, "too_small", "tiny")
    assert [c["name"] for c in client.get("/api/radar").json()["items"]] == [
        "Regshield",
        "Pkgguard",
    ]


def test_rejected_profile_gives_no_line(client, radar, db_session):
    top = radar["top"]
    top.profile_status = ProfileStatus.REJECTED
    db_session.flush()
    item = client.get("/api/radar").json()["items"][0]
    assert (item["line"], item["line_source"]) == ("A registry firewall.", "context")


def test_teaser_skips_candidates_with_no_jfrog_relation(client, radar, db_session):
    w = radar["watching"]
    w.profile = {
        "items": {"jfrog_relation": {"relation": "none", "statement": "x", "signal_ids": [1]}},
        "dropped": [],
    }
    w.profile_status = ProfileStatus.OK
    db_session.flush()
    names = [t["name"] for t in client.get("/api/brief").json()["radar"]]
    assert names == ["Regshield", "Tinytool"]


def test_brief_teaser_and_nav(client, radar):
    brief = client.get("/api/brief").json()
    assert [t["name"] for t in brief["radar"]] == ["Regshield", "Pkgguard", "Tinytool"]
    assert brief["radar"][0]["line"] == "Launched on HN this week."
    assert brief["radar"][1]["status"] == "watching"
    assert client.get("/api/nav").json()["radar_new"] == 2


def test_discovery_signals_stay_out_of_the_brief(client, radar):
    brief = client.get("/api/brief").json()
    ids = {s["id"] for s in brief["since_yesterday"]} | {d["id"] for d in brief["developments"]}
    assert not ids & {s.id for s in radar["signals"]}
    assert client.get("/api/signals").json()["total"] == 0


def test_list_has_profile_lines_relation_and_threshold(client, radar, db_session):
    radar["top"].metrics = {**radar["top"].metrics, "relation": "threatens"}
    db_session.flush()
    data = client.get("/api/radar").json()
    assert data["lower_relevance_below"] == 35
    top = data["items"][0]
    assert top["what_it_is"] == "An open-source registry firewall."
    assert top["why_notable_now"] == "Launched on HN this week."
    assert top["relation"] == "threatens" and top["relation_products"] == ["JFrog Curation"]
    other = data["items"][1]
    assert (other["what_it_is"], other["relation"], other["relation_products"]) == (None, None, [])
    # A rejected profile supplies no statements.
    radar["top"].profile_status = ProfileStatus.REJECTED
    db_session.flush()
    top = client.get("/api/radar").json()["items"][0]
    assert top["what_it_is"] is None and top["why_notable_now"] is None


def test_list_filters_relation_and_kind(client, radar, db_session):
    radar["top"].metrics = {**radar["top"].metrics, "relation": "threatens"}
    radar["watching"].metrics = {**radar["watching"].metrics, "relation": "none"}
    db_session.flush()

    def names(**params):
        r = client.get("/api/radar", params=params)
        assert r.status_code == 200, r.text
        return [c["name"] for c in r.json()["items"]]

    assert names(relation="threatens") == ["Regshield"]
    assert names(relation="threatens,none") == ["Regshield", "Pkgguard"]
    assert names(relation="unknown") == ["Tinytool"]
    assert names(kind="company") == ["Tinytool"]
    assert names(kind="open_source_project", relation="none") == ["Pkgguard"]
    assert client.get("/api/radar?relation=bogus").status_code == 422
    assert client.get("/api/radar?kind=bogus").status_code == 422
    assert client.get("/api/radar?relation=threatens").json()["total"] == 1
