"""Analyst review (S14): approve / correct / reject, the review queue, the scorecard."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.api.review import Reviewed, scorecard_from, week_start
from app.db.enums import (
    FeedbackAction,
    FeedbackTarget,
    ReasonCode,
    ReviewStatus,
    SignalCategory,
)
from app.db.models import Feedback, Signal, signal_entities
from app.pipeline.analyst import apply_overrides
from tests.api.conftest import RAW_SENTINEL

pytestmark = pytest.mark.db


@pytest.fixture
def clean(db_session, client):
    db_session.execute(text("TRUNCATE feedback RESTART IDENTITY"))
    return client


def _feedback(s, sid):
    return s.scalars(
        select(Feedback)
        .where(Feedback.target_type == FeedbackTarget.SIGNAL, Feedback.target_id == sid)
        .order_by(Feedback.id)
    ).all()


def test_approve_writes_feedback_and_review_badge(clean, build, db_session):
    sig = build.signal("Approve me")
    r = clean.post(f"/api/signals/{sig.id}/approve", json={"analyst": "dana"})
    assert r.status_code == 200
    body = r.json()
    assert body["review"] == {
        "status": "approved",
        "by": "dana",
        "at": body["review"]["at"],
        "edited_fields": [],
    }
    assert RAW_SENTINEL not in r.text
    (fb,) = _feedback(db_session, sig.id)
    assert (fb.action, fb.analyst, fb.reason_code) == (FeedbackAction.APPROVE, "dana", None)
    # Approving twice is a conflict; the brief card shows the review.
    assert clean.post(f"/api/signals/{sig.id}/approve", json={}).status_code == 409
    item = clean.get(f"/api/signals/{sig.id}").json()
    assert item["review"]["status"] == "approved"


def test_correct_keeps_model_output_and_applies_fields(clean, build, db_session):
    sonatype = build.entity("sonatype", "Sonatype")
    github = build.entity("github", "GitHub")
    sig = build.signal("Runner image update", impact=3, meta={"triage": {"prompt_version": "t.v1"}})
    sig.enrich_prompt_version = "enrich_signal.v3"
    build.link(sig, sonatype, primary=True)
    db_session.flush()

    r = clean.post(
        f"/api/signals/{sig.id}/correct",
        json={
            "analyst": "dana",
            "reason_code": "impact_too_high",
            "comment": "routine runner image updates don't affect JFrog products",
            "impact": 1,
            "category": "other",
            "primary_entity": "github",
            "relevant": True,  # unchanged: not a correction
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["impact"], out["category"]) == (1, "other")
    assert out["review"]["edited_fields"] == ["category", "impact", "primary_entity"]
    assert [e["slug"] for e in out["entities"] if e["primary"]] == ["github"]
    assert {e["slug"]: e["primary"] for e in out["entities"]} == {"github": True, "sonatype": False}

    db_session.expire_all()
    stored = db_session.get(Signal, sig.id)
    assert stored.status == ReviewStatus.APPROVED
    assert stored.meta["model_output"] == {
        "category": "feature_update",
        "impact": 3,
        "relevant": True,
        "primary_entity": "sonatype",
        "triage_prompt_version": "t.v1",
        "enrich_prompt_version": "enrich_signal.v3",
    }
    assert stored.meta["analyst"]["fields"]["impact"] == {"from": 3, "to": 1}
    (fb,) = _feedback(db_session, sig.id)
    assert fb.action == FeedbackAction.EDIT
    assert fb.reason_code == ReasonCode.IMPACT_TOO_HIGH
    assert set(fb.correction) == {"impact", "category", "primary_entity"}
    assert github.id  # linked as a new primary entity

    # A second correction merges; setting a field back to the model's value drops it.
    r = clean.post(
        f"/api/signals/{sig.id}/correct",
        json={"reason_code": "wrong_category", "category": "feature_update"},
    )
    assert r.json()["review"]["edited_fields"] == ["impact", "primary_entity"]
    db_session.expire_all()
    assert db_session.get(Signal, sig.id).meta["model_output"]["impact"] == 3  # kept, not re-saved


def test_correct_validation(clean, build):
    sig = build.signal(impact=3)
    url = f"/api/signals/{sig.id}/correct"
    assert clean.post(url, json={"reason_code": "other", "impact": 3}).status_code == 422
    assert clean.post(url, json={"reason_code": "other"}).status_code == 422
    assert clean.post(url, json={"reason_code": "other", "impact": 9}).status_code == 422
    assert clean.post(url, json={"reason_code": "nope", "impact": 2}).status_code == 422
    bad_entity = {"reason_code": "wrong_entity", "primary_entity": "nobody"}
    assert clean.post(url, json=bad_entity).status_code == 422
    assert (
        clean.post(
            "/api/signals/999999/correct", json={"reason_code": "other", "impact": 1}
        ).status_code
        == 404
    )


def test_reject_hides_signal_everywhere(clean, build, db_session):
    keep = build.signal("Keep me", impact=4)
    gone = build.signal("Reject me", impact=5)
    r = clean.post(
        f"/api/signals/{gone.id}/reject", json={"analyst": "dana", "reason_code": "not_relevant"}
    )
    assert r.status_code == 200
    assert r.json()["review"]["status"] == "rejected"

    listed = [i["id"] for i in clean.get("/api/signals").json()["items"]]
    assert listed == [keep.id]
    assert clean.get(f"/api/signals/{gone.id}").status_code == 404
    brief = clean.get("/api/brief").text
    assert "Reject me" not in brief and "Keep me" in brief
    assert clean.get("/api/nav").json()["review_queue"] == 1
    # Still in the DB; a second reject or a correction conflicts.
    assert db_session.get(Signal, gone.id) is not None
    assert clean.post(f"/api/signals/{gone.id}/reject", json={}).status_code == 409
    corr = {"reason_code": "other", "impact": 1}
    assert clean.post(f"/api/signals/{gone.id}/correct", json=corr).status_code == 409
    (fb,) = _feedback(db_session, gone.id)
    assert (fb.action, fb.reason_code) == (FeedbackAction.REJECT, ReasonCode.NOT_RELEVANT)


def test_correct_relevant_no_rejects(clean, build, db_session):
    sig = build.signal()
    r = clean.post(
        f"/api/signals/{sig.id}/correct", json={"reason_code": "not_relevant", "relevant": False}
    )
    assert r.json()["review"]["status"] == "rejected"
    assert clean.get("/api/signals?relevant=false").json()["total"] == 0


def test_review_queue_order_and_progress(clean, build):
    low = build.signal("low", impact=2, confidence=0.9)
    high = build.signal("high", impact=5, confidence=0.7)
    mid = build.signal("mid", impact=4, confidence=0.8)
    done = build.signal("done", impact=5, confidence=0.9)
    gone = build.signal("gone", impact=5, confidence=0.9)
    build.signal("irrelevant", relevant=False)
    clean.post(f"/api/signals/{done.id}/approve", json={})
    clean.post(f"/api/signals/{gone.id}/reject", json={})
    body = clean.get("/api/review").json()
    assert [i["id"] for i in body["items"]] == [high.id, mid.id, low.id]
    assert body["progress"] == {"reviewed": 2, "total": 5}
    assert RAW_SENTINEL not in clean.get("/api/review").text


def test_scorecard_endpoint(clean, build):
    a, b, c, d = (build.signal(impact=4) for _ in range(4))
    clean.post(f"/api/signals/{a.id}/approve", json={})
    clean.post(
        f"/api/signals/{b.id}/correct",
        json={"reason_code": "impact_too_high", "impact": 2, "category": "other"},
    )
    clean.post(f"/api/signals/{c.id}/correct", json={"reason_code": "impact_too_low", "impact": 5})
    clean.post(f"/api/signals/{d.id}/reject", json={"reason_code": "not_relevant"})
    body = clean.get("/api/feedback/scorecard").json()
    o = body["overall"]
    assert (o["reviewed"], o["approved"], o["corrected"], o["rejected"]) == (4, 1, 2, 1)
    assert (o["approved_pct"], o["corrected_pct"], o["rejected_pct"]) == (25.0, 50.0, 25.0)
    assert o["fields"]["impact"] == {"corrected": 2, "rate": 0.5, "mean_delta": -0.5}
    assert o["fields"]["category"]["corrected"] == 1
    assert o["fields"]["relevance"]["corrected"] == 1
    assert o["fields"]["entity"]["corrected"] == 0
    assert {r["code"] for r in o["reasons"]} == {
        "impact_too_high",
        "impact_too_low",
        "not_relevant",
    }
    assert len(body["weeks"]) == 1 and body["weeks"][0]["reviewed"] == 4


def test_scorecard_math_per_week():
    mon = datetime(2026, 10, 12, 9, tzinfo=UTC)
    nxt = datetime(2026, 10, 20, 9, tzinfo=UTC)
    items = [
        Reviewed("approved", {}, None, mon),
        Reviewed("corrected", {"impact": {"from": 4, "to": 1}}, None, mon),
        Reviewed(
            "corrected",
            {"impact": {"from": 2, "to": 4}, "relevant": {"from": True, "to": False}},
            None,
            nxt,
        ),
        Reviewed("rejected", {}, "duplicate", nxt),
    ]
    reasons = [
        ("impact_too_high", mon),
        ("impact_too_low", nxt),
        ("duplicate", nxt),
        ("duplicate", nxt),
    ]
    out = scorecard_from(items, reasons)
    assert out.overall.fields.impact.mean_delta == -0.5  # (-3 + 2) / 2
    assert (
        out.overall.fields.relevance.corrected == 1
    )  # a duplicate rejection is not a relevance error
    assert [r.code for r in out.overall.reasons][0] == ReasonCode.DUPLICATE
    assert [w.week_start for w in out.weeks] == ["2026-10-19", "2026-10-12"]
    assert out.weeks[1].approved_pct == 50.0
    assert week_start(datetime(2026, 10, 18, 23, tzinfo=UTC)) == "2026-10-12"  # Sunday
    empty = scorecard_from([], [])
    assert empty.overall.reviewed == 0 and empty.overall.approved_pct == 0.0


def test_overrides_survive_re_enrichment(clean, build, db_session):
    ent = build.entity("sonatype", "Sonatype")
    other = build.entity("github", "GitHub")
    sig = build.signal(impact=4)
    build.link(sig, ent, primary=True)
    clean.post(
        f"/api/signals/{sig.id}/correct",
        json={"reason_code": "impact_too_high", "impact": 2, "primary_entity": "github"},
    )
    # The pipeline writes fresh model values (impact 5, category, primary back to sonatype) …
    db_session.execute(
        text("UPDATE signals SET impact = 5, category = 'pricing_change' WHERE id = :i"),
        {"i": sig.id},
    )
    db_session.execute(
        signal_entities.update()
        .where(signal_entities.c.signal_id == sig.id)
        .values(is_primary=signal_entities.c.entity_id == ent.id)
    )
    assert apply_overrides(db_session, sig.id, "enrich", "enrich_signal.v4")
    db_session.expire_all()
    s = db_session.get(Signal, sig.id)
    # … the analyst's values win, the fresh model output is kept.
    assert s.impact == 2 and s.category == SignalCategory.PRICING_CHANGE
    assert s.meta["model_output"]["impact"] == 5
    assert s.meta["model_output"]["primary_entity"] == "sonatype"
    assert s.meta["model_output"]["enrich_prompt_version"] == "enrich_signal.v4"
    assert s.meta["model_output"]["relevant"] is True  # triage field untouched by an enrich run
    primary = db_session.scalar(
        select(signal_entities.c.entity_id).where(
            signal_entities.c.signal_id == sig.id, signal_entities.c.is_primary
        )
    )
    assert primary == other.id
    # No correction → nothing to do.
    plain = build.signal()
    assert not apply_overrides(db_session, plain.id, "enrich")
