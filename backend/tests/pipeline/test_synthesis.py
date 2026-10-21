"""Daily brief + battlecards with a scripted fake LLM: citation checks (invalid / wrong-entity ids
drop the whole item), second-model check, capability evidence, matrix upsert, idempotency, due
logic, one failing competitor."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.enums import Relationship, ReportKind, Verification
from app.db.models import CapabilityMatrix, Entity, Report, Signal, signal_entities
from app.pipeline.synthesis.battlecards import battlecards_step
from app.pipeline.synthesis.brief import brief_step
from app.pipeline.synthesis.schemas import check_schema
from tests.pipeline.factories import (
    T0,
    FakeLLM,
    claim_keys_in,
    ctx,
    ensure_capability,
    entity,
    signal,
    signal_ids_in,
    source,
)

pytestmark = pytest.mark.db

NOW = T0 + timedelta(days=2)


def _usable(s, src, title, *, impact=3, days=0.0, links=(), takeaway="Point for JFrog Xray."):
    sig = signal(s, src, title, days=days)
    s.execute(
        update(Signal)
        .where(Signal.id == sig.id)
        .values(
            relevant=True, category="feature_update", impact=impact, confidence=0.8,
            summary=f"{title}: summary.", why_jfrog_cares="Matters for JFrog Artifactory.",
            takeaway=takeaway, enriched_at=T0, verification=Verification.SUPPORTED,
        )
    )  # fmt: skip
    for ent, primary in links:
        s.execute(
            insert(signal_entities).values(signal_id=sig.id, entity_id=ent.id, is_primary=primary)
        )
    return sig.id


@pytest.fixture
def world(collect_db):
    with collect_db.begin() as s:
        for slug in ("sbom", "package-curation"):
            ensure_capability(s, slug)
        jfrog = Entity(
            type="company", name="JFrog", slug="jfrog", relationship=Relationship.SELF,
            is_self=True, review_status="approved",
        )  # fmt: skip
        s.add(jfrog)
        s.flush()
        entity(s, "jfrog-xray", "JFrog Xray", parent=jfrog)
        sonatype = entity(s, "sonatype", "Sonatype")
        firewall = entity(s, "sonatype-firewall", "Sonatype Repository Firewall", parent=sonatype)
        github = entity(s, "github", "GitHub")
        s.execute(
            update(Entity)
            .where(Entity.id.in_([sonatype.id, github.id]))
            .values(relationship=Relationship.COMPETITOR)
        )
        chainguard = entity(s, "chainguard", "Chainguard")
        s.execute(
            update(Entity)
            .where(Entity.id == chainguard.id)
            .values(relationship=Relationship.COMPETITOR)
        )
        blog = source(s, "Sonatype Blog", trust=0.9, entity=sonatype)
        gh = source(s, "GitHub Changelog", trust=0.9, entity=github)
        ids = {
            "fw": _usable(s, blog, "Firewall 2 launch", impact=4, links=[(firewall, True)]),
            "sbom": _usable(s, blog, "Sonatype SBOM import", days=0.5, links=[(sonatype, True)]),
            "mention": _usable(
                s, gh, "GitHub notes Sonatype", days=1, links=[(github, True), (sonatype, False)]
            ),
            "gh": _usable(s, gh, "GitHub secret scanning", days=1.2, links=[(github, True)]),
            # Chainguard only mentioned: no battlecard.
            "cg": _usable(s, gh, "GitHub cites Chainguard", links=[(chainguard, False)]),
        }
        hidden = signal(s, blog, "Pending re-enrichment", days=1)  # not enriched: not usable
        ids["hidden"] = hidden.id
    return collect_db, ids


def _ctx(factory, llm, force=frozenset()):
    c = ctx(llm=llm, factory=factory, force=force)
    c.now = NOW
    return c


def _step(factory, step, llm, force=frozenset()):
    with factory.begin() as s:
        return step(s, _ctx(factory, llm, force))


def _reports(factory, kind):
    with factory() as s:
        return s.scalars(select(Report).where(Report.kind == kind).order_by(Report.id)).all()


# ---------- daily brief ----------


def test_brief_skips_without_signals(collect_db):
    llm = FakeLLM()
    assert _step(collect_db, brief_step, llm) == {"skipped": "no relevant signals"}
    assert llm.calls == []


def test_brief_stored_once_per_day_and_force_regenerates(world):
    factory, ids = world
    llm = FakeLLM()
    counts = _step(factory, brief_step, llm)
    assert counts["stored"] == 1 and counts["dropped"] == 0
    (report,) = _reports(factory, ReportKind.DAILY_BRIEF)
    assert report.content["headline"] and len(report.content["overview_sentences"]) == 3
    assert ids["hidden"] not in report.content["input_ids"]
    assert set(report.signal_ids) <= set(report.content["input_ids"])
    assert llm.count("daily_brief") == 1 and llm.count("verify_synthesis") == 1

    assert _step(factory, brief_step, llm) == {"skipped": "brief exists for today"}
    assert llm.count("daily_brief") == 1
    _step(factory, brief_step, llm, force=frozenset({"brief"}))
    assert len(_reports(factory, ReportKind.DAILY_BRIEF)) == 2


def test_sentence_with_one_invalid_id_is_dropped_whole(world):
    factory, ids = world

    def brief(call):
        good = signal_ids_in(call)
        return {
            "headline": {"text": "Sonatype ships Firewall 2", "signal_ids": [ids["fw"]]},
            "overview": [
                {"text": "Sonatype launched Firewall 2 today.", "signal_ids": [ids["fw"]]},
                {"text": "GitHub changed secret scanning.", "signal_ids": [good[-1]]},
                # one valid id + one invented id: the whole sentence goes
                {"text": "Both vendors raised prices.", "signal_ids": [ids["fw"], 999999]},
            ],
        }

    counts = _step(factory, brief_step, FakeLLM(daily_brief=brief))
    (report,) = _reports(factory, ReportKind.DAILY_BRIEF)
    assert counts["dropped"] == 1
    texts = [s["text"] for s in report.content["overview_sentences"]]
    assert "Both vendors raised prices." not in texts and len(texts) == 2
    (drop,) = report.content["dropped"]
    assert drop["reason"] == "invalid_citation" and drop["invalid_ids"] == {"999999": "unknown"}


def test_hidden_signal_id_counts_as_invalid(world):
    factory, ids = world

    def brief(call):
        return {
            "headline": {"text": "Sonatype ships Firewall 2", "signal_ids": [ids["hidden"]]},
            "overview": [
                {"text": "Sonatype launched Firewall 2 today.", "signal_ids": [ids["fw"]]},
                {"text": "GitHub changed secret scanning.", "signal_ids": [ids["gh"]]},
                {"text": "Sonatype added SBOM import.", "signal_ids": [ids["sbom"]]},
            ],
        }

    llm = FakeLLM(daily_brief=brief)
    counts = _step(factory, brief_step, llm)
    assert counts["rejected"] == 1 and _reports(factory, ReportKind.DAILY_BRIEF) == []
    assert llm.count("verify_synthesis") == 0  # nothing to save: no paid check
    assert any("headline: invalid_citation" in r for r in counts["reasons"])


def test_verifier_partial_drops_the_sentence_and_can_reject_the_brief(world):
    factory, _ = world

    def check(call):
        return {
            "items": [
                {
                    "key": k,
                    "verdict": "partial" if k in ("overview.1", "overview.2") else "supported",
                    "note": "The price is not in the cited signal.",
                }
                for k in claim_keys_in(call)
            ]
        }

    counts = _step(factory, brief_step, FakeLLM(verify_synthesis=check))
    assert counts["rejected"] == 1  # only one point left
    assert any("overview.1: verifier_partial" in r for r in counts["reasons"])


def test_check_schema_requires_every_key_once():
    model = check_schema(["headline", "overview.1"])
    with pytest.raises(ValueError, match="at least 2 items"):
        model.model_validate({"items": [{"key": "headline", "verdict": "supported", "note": "ok"}]})
    twice = {"key": "headline", "verdict": "supported", "note": "ok"}
    with pytest.raises(ValueError, match=r"missing \['overview.1'\], repeated \['headline'\]"):
        model.model_validate({"items": [twice, twice]})


# ---------- battlecards ----------


def _cards(factory):
    with factory() as s:
        rows = s.execute(
            select(Entity.slug, Report)
            .join(Report, Report.entity_id == Entity.id)
            .where(Report.kind == ReportKind.BATTLECARD)
            .order_by(Report.id)
        ).all()
    return rows


def _matrix(factory, slug):
    with factory() as s:
        return s.scalars(
            select(CapabilityMatrix)
            .join(Entity, Entity.id == CapabilityMatrix.entity_id)
            .where(Entity.slug == slug)
        ).all()


def test_only_competitors_with_primary_signals_get_a_card(world):
    factory, _ = world
    llm = FakeLLM()
    counts = _step(factory, battlecards_step, llm)
    assert counts["stored"] == 2 and counts["not_eligible"] == 1  # chainguard: mention only
    assert sorted(slug for slug, _ in _cards(factory)) == ["github", "sonatype"]
    assert llm.count("battlecard") == 2 and llm.count("verify_synthesis") == 2


def _sonatype_card(ids, **overrides):
    def card(call):
        if "Competitor: Sonatype" not in call.messages[-1]["content"]:
            return {"positioning": {"text": "GitHub is a platform.", "signal_ids": [ids["gh"]]}}
        out = {
            "positioning": {"text": "Sonatype sells a safe registry.", "signal_ids": [ids["fw"]]},
            "strengths": [
                {"text": "Firewall 2 blocks malicious packages.", "signal_ids": [ids["fw"]]},
                # GitHub's own signal in Sonatype's card: wrong entity → dropped
                {"text": "Ships secret scanning.", "signal_ids": [ids["gh"]]},
            ],
            "weaknesses": [],
            "opportunities_for_jfrog": [],
            "blind_spots_for_jfrog": [],
            "capabilities": [
                {"capability": "package-curation", "level": "full",
                 "evidence_signal_id": ids["fw"], "note": "Firewall 2 blocks packages."},
                {"capability": "sbom", "level": "partial",
                 "evidence_signal_id": 999999, "note": "invented evidence"},
                {"capability": "sbom", "level": "full",
                 "evidence_signal_id": ids["mention"], "note": "Sonatype only mentioned."},
            ],
        }  # fmt: skip
        return {**out, **overrides}

    return card


def test_battlecard_drops_wrong_entity_items_and_levels_without_valid_evidence(world):
    factory, ids = world
    counts = _step(factory, battlecards_step, FakeLLM(battlecard=_sonatype_card(ids)))
    assert counts["sonatype"]["levels_accepted"] == 1 and counts["sonatype"]["levels_dropped"] == 2

    card = dict(_cards(factory))["sonatype"].content
    assert [i["text"] for i in card["strengths"]] == ["Firewall 2 blocks malicious packages."]
    (wrong,) = card["dropped"]
    assert wrong["invalid_ids"] == {str(ids["gh"]): "wrong_entity"}
    reasons = sorted(d["reason"] for d in card["capabilities_dropped"])
    assert reasons == ["evidence_not_about_competitor", "invalid_citation"]

    (cell,) = _matrix(factory, "sonatype")  # sbom stays unknown: no row
    assert cell.level == "full" and cell.evidence_signal_id == ids["fw"]
    assert cell.last_verified_at == NOW and cell.previous_level is None


def test_level_change_records_previous_level(world):
    factory, ids = world
    _step(factory, battlecards_step, FakeLLM(battlecard=_sonatype_card(ids)))
    partial = _sonatype_card(
        ids,
        capabilities=[
            {"capability": "package-curation", "level": "partial",
             "evidence_signal_id": ids["fw"], "note": "Only npm for now."},
        ],
    )  # fmt: skip
    _step(factory, battlecards_step, FakeLLM(battlecard=partial), force=frozenset({"battlecards"}))
    (cell,) = _matrix(factory, "sonatype")
    assert (cell.level, cell.previous_level, cell.level_changed_at) == ("partial", "full", NOW)


def test_verifier_drops_unsupported_levels(world):
    factory, ids = world

    def check(call):
        return {
            "items": [
                {"key": k, "verdict": "unsupported" if k.startswith("capability") else "supported",
                 "note": "The signal does not show this level."}
                for k in claim_keys_in(call)
            ]
        }  # fmt: skip

    _step(
        factory, battlecards_step, FakeLLM(battlecard=_sonatype_card(ids), verify_synthesis=check)
    )
    assert _matrix(factory, "sonatype") == []
    card = dict(_cards(factory))["sonatype"].content
    assert card["capabilities_accepted"] == []
    assert any(d["reason"] == "verifier_unsupported" for d in card["capabilities_dropped"])


def test_one_failing_competitor_does_not_stop_the_others(world):
    factory, ids = world

    def card(call):
        if "Competitor: GitHub" in call.messages[-1]["content"]:
            raise RuntimeError("provider down")
        return _sonatype_card(ids)(call)

    counts = _step(factory, battlecards_step, FakeLLM(battlecard=card))
    assert counts["stored"] == 1 and counts["failed"] == 1
    assert "provider down" in counts["errors"]["github"]
    assert [slug for slug, _ in _cards(factory)] == ["sonatype"]


def test_cards_are_not_redone_until_due(world):
    factory, ids = world
    llm = FakeLLM()
    _step(factory, battlecards_step, llm)
    counts = _step(factory, battlecards_step, llm)
    assert counts["not_due"] == 2 and llm.count("battlecard") == 2

    with factory.begin() as s:  # a new impact-4 Sonatype signal → Sonatype is due again
        firewall = s.scalar(select(Entity).where(Entity.slug == "sonatype-firewall"))
        _usable(s, None, "Firewall 3", impact=4, days=1.5, links=[(firewall, True)])
    counts = _step(factory, battlecards_step, llm)
    assert counts["stored"] == 1 and counts["not_due"] == 1
    assert counts["sonatype"]["due"].startswith("new impact >= 4")
    with factory() as s:
        n = s.scalar(select(func.count()).select_from(Report).where(Report.entity_id.is_not(None)))
    assert n == 3


def test_points_longer_than_110_chars_fail_validation():
    from pydantic import ValidationError

    from app.pipeline.synthesis.schemas import DailyBrief

    point = {"text": "Short point about Sonatype.", "signal_ids": [1]}
    brief = {"headline": {"text": "Sonatype moves", "signal_ids": [1]}, "overview": [point] * 3}
    DailyBrief.model_validate(brief)
    with pytest.raises(ValidationError, match="110"):
        DailyBrief.model_validate(
            {**brief, "overview": [point, point, {**point, "text": "x" * 111}]}
        )
    with pytest.raises(ValidationError):
        DailyBrief.model_validate({**brief, "overview": [point, point]})  # exactly 3


def test_verifier_gets_jfrog_levels_as_trusted_context(world):
    factory, _ = world
    llm = FakeLLM()
    _step(factory, brief_step, llm)
    (check,) = [c for c in llm.calls if c.operation == "verify_synthesis"]
    user = check.messages[-1]["content"]
    assert "JFrog's capability levels (trusted" in user
    assert "hosted-pipelines | Hosted CI/CD pipelines | none |" in user


def test_battlecards_for_selected_companies_only(world):
    factory, _ = world
    llm = FakeLLM()
    c = _ctx(factory, llm)
    c.only_entities = frozenset({"sonatype"})
    with factory.begin() as s:
        counts = battlecards_step(s, c)
    assert counts["stored"] == 1 and "sonatype" in counts and "github" not in counts
