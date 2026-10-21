"""Triage → enrich → verify with a scripted fake LLM (no network): gate, writes, quote check and
retry, entity confirmation, verification mapping, duplicate inheritance, idempotency, failures."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from app.core.llm import BudgetExceeded
from app.db.enums import Relationship, Verification
from app.db.models import Entity, Signal, Source, signal_capabilities, signal_entities
from app.pipeline.enrich import enrich_signals
from app.pipeline.quotes import normalize, quote_in_text
from app.pipeline.schemas import TriageResult, enrich_schema
from app.pipeline.triage import triage_signals
from app.pipeline.verify import verification_notes, verify_signals
from tests.pipeline.factories import (
    FakeLLM,
    ctx,
    default_enrich,
    ensure_capability,
    entity,
    signal,
    source,
)

pytestmark = pytest.mark.db

TEXT = (
    "Sonatype launched Repository Firewall 2 today. It blocks malicious npm packages before "
    "download. Pricing starts at $10 per developer."
)


@pytest.fixture
def world(collect_db):
    """Committed rows (LLM stages write each signal in its own transaction)."""
    with collect_db.begin() as s:
        ensure_capability(s, "malicious-package-detection")
        sonatype = entity(s, "sonatype", "Sonatype")
        entity(s, "sonatype-firewall", "Sonatype Repository Firewall", parent=sonatype)
        entity(s, "snyk", "Snyk")
        jfrog = Entity(
            type="company", name="JFrog", slug="jfrog", relationship=Relationship.SELF,
            is_self=True, review_status="approved",
        )  # fmt: skip
        s.add(jfrog)
        s.flush()
        entity(s, "jfrog-curation", "JFrog Curation", parent=jfrog)
        blog = source(s, "Sonatype Blog", trust=0.9, entity=sonatype)
        press = source(s, "Press", trust=0.7)
        ids = {
            "firewall": signal(s, blog, "Firewall 2 launch", text=TEXT).id,
            "llm": signal(s, press, "New LLM prices", text="A model got cheaper. Nothing else.").id,
        }
    return collect_db, ids


def _get(factory, signal_id) -> Signal:
    with factory() as s:
        return s.get_one(Signal, signal_id)


def _links(factory, signal_id) -> dict[str, bool]:
    with factory() as s:
        rows = s.execute(
            select(Entity.slug, signal_entities.c.is_primary)
            .join(Entity, Entity.id == signal_entities.c.entity_id)
            .where(signal_entities.c.signal_id == signal_id)
        ).all()
    return dict(rows)


def _run(factory, llm, *steps, force=frozenset()):
    c = ctx(llm=llm, factory=factory, force=force)
    out = []
    for step in steps:
        with factory.begin() as s:
            out.append(step(s, c))
    return out


def _irrelevant_llm(call):
    if "LLM" in call.title:
        return {"relevant": False, "reason": "LLM API pricing", "segments": []}
    return {"relevant": True, "reason": "registry firewall", "segments": ["supply_chain_security"]}


# ---------- triage ----------


def test_triage_gate_marks_and_skips_irrelevant(world):
    factory, ids = world
    llm = FakeLLM(triage=_irrelevant_llm)
    triage, enrich = _run(factory, llm, triage_signals, enrich_signals)

    assert triage["relevant"] == 1 and triage["irrelevant"] == 1
    kept = _get(factory, ids["llm"])
    assert kept.relevant is False and kept.enriched_at is None  # kept, not enriched
    assert kept.meta["triage"]["reason"] == "LLM API pricing"
    assert kept.meta["triage"]["prompt_version"] == "triage.v1"
    assert enrich["enriched"] == 1 and llm.count("enrich_signal") == 1


def test_triage_schema_requires_segments_when_relevant():
    with pytest.raises(ValidationError, match="at least one segment"):
        TriageResult(relevant=True, reason="x", segments=[])
    assert TriageResult(relevant=False, reason="x").segments == []


# ---------- enrich ----------


def _enrich_firewall(call):
    out = default_enrich(call)
    out.update(
        category="product_launch",
        impact=4,
        evidence_quote="It blocks malicious npm packages before download.",
        entities=[
            {"slug": "sonatype-firewall", "role": "primary"},
            {"slug": "jfrog-curation", "role": "mention"},
        ],
        new_entities=[
            {"name": "Acme Scan", "kind": "product", "context": "new scanner"},
            {"name": "snyk", "kind": "company", "context": "already tracked: must be dropped"},
        ],
        capabilities=["malicious-package-detection"],
    )
    return out


def test_enrich_writes_fields_links_and_capabilities(world):
    factory, ids = world
    sid = ids["firewall"]
    # A rule-based link the LLM will not confirm (S5 linker output).
    with factory.begin() as s:
        snyk = s.scalar(select(Entity.id).where(Entity.slug == "snyk"))
        s.execute(signal_entities.insert().values(signal_id=sid, entity_id=snyk, is_primary=False))
        s.execute(
            update(Signal)
            .where(Signal.id == sid)
            .values(meta={"entity_links": {"links": [{"entity_id": snyk}]}})
        )
    llm = FakeLLM(triage=_irrelevant_llm, enrich_signal=_enrich_firewall)
    _run(factory, llm, triage_signals, enrich_signals)

    sig = _get(factory, sid)
    assert sig.category == "product_launch" and sig.impact == 4
    assert sig.evidence_quote_verified is True and sig.enrich_prompt_version == "enrich_signal.v1"
    assert sig.verification == Verification.PENDING and sig.enriched_at is not None
    # Source entity stays primary; the LLM's links replace the unconfirmed rule link.
    assert _links(factory, sid) == {
        "sonatype": True, "sonatype-firewall": True, "jfrog-curation": False,
    }  # fmt: skip
    assert sig.meta["entity_review"] == {
        "confirmed": [], "added": ["jfrog-curation", "sonatype-firewall"], "dropped": ["snyk"],
    }  # fmt: skip
    assert [e["name"] for e in sig.meta["enrich"]["new_entities"]] == ["Acme Scan"]
    with factory() as s:
        n_caps = s.scalar(
            select(signal_capabilities.c.capability_id)
            .where(signal_capabilities.c.signal_id == sid)
            .limit(1)
        )
    assert n_caps is not None


def test_enrich_schema_rejects_unknown_slugs():
    schema = enrich_schema(["sonatype"], ["oci-registry"])
    good = {
        "category": "other", "summary": "x" * 50, "why_jfrog_cares": "y" * 50, "impact": 2,
        "impact_rationale": "r", "evidence_quote": "a quote here", "is_claim": False,
        "entities": [{"slug": "sonatype", "role": "primary"}], "capabilities": ["oci-registry"],
    }  # fmt: skip
    schema.model_validate(good)
    with pytest.raises(ValidationError):
        schema.model_validate({**good, "entities": [{"slug": "evil-corp", "role": "primary"}]})
    with pytest.raises(ValidationError):
        schema.model_validate({**good, "capabilities": ["mind-control"]})
    with pytest.raises(ValidationError):
        schema.model_validate({**good, "impact": 6})


# ---------- quote check ----------


def test_quote_normalization():
    text = "He said “ship it” – and   it\nshipped. Done."
    assert quote_in_text('He said "ship it" - and it shipped.', text)
    assert quote_in_text("  “ship it” – and it shipped. ", text)
    assert not quote_in_text("He said ship it and it shipped.", text)  # paraphrase
    assert not quote_in_text("shipped.", text)  # under 10 chars: too short to be evidence
    assert normalize("a b") == "a b"


def test_quote_failure_is_retried_once_then_succeeds(world):
    factory, ids = world

    def enrich(call):
        out = _enrich_firewall(call)
        if call.attempt == 1:
            out["evidence_quote"] = "Sonatype is the market leader in firewalls."  # not in text
        return out

    llm = FakeLLM(triage=_irrelevant_llm, enrich_signal=enrich)
    _run(factory, llm, triage_signals, enrich_signals)

    sig = _get(factory, ids["firewall"])
    assert sig.evidence_quote_verified is True and sig.meta["enrich"]["quote_attempts"] == 2
    retry = [c for c in llm.calls if c.operation == "enrich_signal"][1]
    assert "not a verbatim excerpt" in retry.messages[-1]["content"]


def test_quote_failing_twice_is_stored_flagged(world):
    factory, ids = world

    def enrich(call):
        return {**_enrich_firewall(call), "evidence_quote": "An invented sentence, twice."}

    llm = FakeLLM(triage=_irrelevant_llm, enrich_signal=enrich)
    _, counts = _run(factory, llm, triage_signals, enrich_signals)

    sig = _get(factory, ids["firewall"])
    assert sig.evidence_quote_verified is False and sig.evidence_quote is not None
    assert counts["enriched_quote_unverified"] == 1 and llm.count("enrich_signal") == 2


# ---------- verify ----------


def test_verification_mapping(world):
    factory, ids = world
    verdict = {
        "verdict": "partial",
        "notes": "Launch confirmed; the price is not in the source.",
        "unsupported_claims": ["costs $5"],
    }
    llm = FakeLLM(triage=_irrelevant_llm, verify_signal=verdict)
    _, _, counts = _run(factory, llm, triage_signals, enrich_signals, verify_signals)

    sig = _get(factory, ids["firewall"])
    assert counts["partial"] == 1
    assert sig.verification == Verification.PARTIAL
    assert sig.verification_notes == (
        "Launch confirmed; the price is not in the source. Unsupported: costs $5"
    )
    assert sig.meta["verify"]["prompt_version"] == "verify_signal.v1"
    assert llm.calls[-1].task == "verify"  # the verify tier (a different provider)


def test_verification_notes_without_claims():
    from app.pipeline.schemas import VerifyResult

    assert verification_notes(VerifyResult(verdict="supported", notes=" ok ")) == "ok"


# ---------- duplicates ----------


def test_duplicates_inherit_from_canonical(world):
    factory, ids = world
    canonical = ids["firewall"]
    with factory.begin() as s:
        press = s.scalar(select(Signal.source_id).where(Signal.id == ids["llm"]))
        dup = signal(s, None, "Firewall 2 launch (reprint)", text="Reprint: " + TEXT)
        s.execute(
            update(Signal)
            .where(Signal.id.in_([canonical, dup.id]))
            .values(dedup_cluster_id=canonical)
        )
        s.execute(update(Signal).where(Signal.id == dup.id).values(source_id=press))
        dup_id = dup.id
    llm = FakeLLM(triage=_irrelevant_llm, enrich_signal=_enrich_firewall)
    _run(factory, llm, triage_signals, enrich_signals, verify_signals)

    head, copy = _get(factory, canonical), _get(factory, dup_id)
    assert [c.title for c in llm.calls if c.operation == "enrich_signal"] == ["Firewall 2 launch"]
    for field in ("relevant", "category", "summary", "impact", "verification", "evidence_quote"):
        assert getattr(copy, field) == getattr(head, field), field
    assert copy.meta["inherited_from"] == canonical
    assert copy.evidence_quote_verified is True  # checked against the duplicate's own text
    assert "sonatype-firewall" in _links(factory, dup_id)


# ---------- idempotency ----------


def test_rerun_makes_no_calls_and_version_or_force_redoes(world):
    factory, ids = world
    llm = FakeLLM(triage=_irrelevant_llm)
    steps = (triage_signals, enrich_signals, verify_signals)
    _run(factory, llm, *steps)
    first = len(llm.calls)

    _run(factory, llm, *steps)
    assert len(llm.calls) == first  # nothing new

    # An older prompt version on record → re-enrich (and therefore re-verify) that signal.
    with factory.begin() as s:
        s.execute(
            update(Signal)
            .where(Signal.id == ids["firewall"])
            .values(enrich_prompt_version="enrich_signal.v0")
        )
    _run(factory, llm, *steps)
    assert llm.count("enrich_signal") == 2 and llm.count("verify_signal") == 2
    assert llm.count("triage") == 2  # triage untouched

    _run(factory, llm, *steps, force=frozenset({"triage"}))
    assert llm.count("triage") == 4 and llm.count("enrich_signal") == 2


# ---------- failures ----------


def test_one_failing_signal_does_not_stop_the_stage(world):
    factory, ids = world
    with factory.begin() as s:
        blog = s.scalar(select(Source).where(Source.name == "Sonatype Blog"))
        for i in range(3):
            signal(s, blog, f"Item {i}", text=f"Sonatype ships item number {i} today. More.")

    def enrich(call):
        if call.title == "Item 1":
            raise RuntimeError("provider exploded")
        return default_enrich(call)

    llm = FakeLLM(enrich_signal=enrich)
    _, counts = _run(factory, llm, triage_signals, enrich_signals)

    assert counts["failed"] == 1 and counts["enriched"] == 4
    assert counts["errors"][0]["error"] == "RuntimeError: provider exploded"
    with factory() as s:
        broken = s.scalar(select(Signal).where(Signal.title == "Item 1"))
        assert broken.enriched_at is None
        assert "provider exploded" in broken.meta["enrich_error"]["error"]


def test_budget_exhaustion_stops_the_stage(world):
    factory, _ = world

    def broke(call):
        raise BudgetExceeded("daily budget reached")

    counts, = _run(factory, FakeLLM(triage=broke), triage_signals)  # fmt: skip
    assert counts["stopped"].startswith("budget") and counts["failed"] == 0


def test_workers_see_the_run_context(world):
    """llm_calls.run_id comes from a contextvar; worker threads must carry it."""
    from app.core.llm import current_context, llm_context

    factory, _ = world
    seen: list[int | None] = []

    def triage(call):
        seen.append(current_context().run_id)
        return {"relevant": False, "reason": "x", "segments": []}

    with llm_context(run_id=4242):
        _run(factory, FakeLLM(triage=triage), triage_signals)
    assert seen and set(seen) == {4242}
