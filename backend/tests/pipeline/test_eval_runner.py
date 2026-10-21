"""Eval runner on a real (throwaway) DB with the scripted fake LLM: it never writes to signals,
uses the pipeline's per-signal functions, runs the thinking A/B against the other level, and
compares with silver labels. Plus the dedup pair population query on synthetic vectors."""

import pytest
from sqlalchemy import select

from app.db.models import PipelineRun, Signal
from app.eval import pairs as pr
from app.eval import runner
from app.eval.files import LabelRow, write_labels
from tests.pipeline.factories import (
    MODEL,
    FakeLLM,
    ensure_capability,
    entity,
    first_sentence,
    signal,
    source,
    vec,
)

pytestmark = pytest.mark.db

TEXT = "Sonatype launched Firewall 2 today. It blocks malicious npm packages before download."


@pytest.fixture
def eval_world(collect_db, tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_DIR", str(tmp_path))
    with collect_db.begin() as s:
        ensure_capability(s)
        sonatype = entity(s, "sonatype", "Sonatype")
        entity(s, "sonatype-firewall", "Sonatype Firewall", parent=sonatype)
        blog = source(s, "Sonatype Blog", trust=0.9, entity=sonatype)
        press = source(s, "Press")
        a = signal(s, blog, "Firewall 2 launch", text=TEXT)
        b = signal(s, press, "LLM prices", text="A model got cheaper. Nothing else changed here.")
        blog.is_active = False  # out of scope: the eval still processes it
        ids = (a.id, b.id)
    labels = [
        LabelRow(
            id=ids[0],
            title="Firewall 2 launch",
            source="Sonatype Blog",
            date="",
            url="u",
            in_scope="n",
            stratum="test",
            relevant="y",
            category="product_launch",
            impact="4",
            primary_entity="sonatype-firewall",
            label_source="model:test",
        ),
        LabelRow(
            id=ids[1],
            title="LLM prices",
            source="Press",
            date="",
            url="u",
            in_scope="y",
            stratum="test",
            relevant="n",
            label_source="model:test",
        ),
    ]
    write_labels(labels)
    return collect_db, ids


def _snapshot(factory):
    cols = (
        Signal.id,
        Signal.relevant,
        Signal.category,
        Signal.summary,
        Signal.impact,
        Signal.verification,
        Signal.enriched_at,
        Signal.meta,
    )
    with factory() as s:
        return s.execute(select(*cols).order_by(Signal.id)).all()


def test_eval_pass_compares_with_silver_labels_and_never_writes_signals(eval_world, monkeypatch):
    factory, (firewall, llm) = eval_world
    fake = FakeLLM(
        triage=lambda c: {"relevant": True, "reason": "r", "segments": ["artifact_management"]},
        enrich_signal=lambda c: {
            "category": "feature_update",
            "impact": 5,
            "summary": "Sonatype launched Firewall 2, which blocks malicious npm packages.",
            "why_jfrog_cares": "It competes directly with JFrog Curation at the repository layer.",
            "takeaway": "Firewall 2 competes head-on with JFrog Curation.",
            "impact_rationale": "5",
            "evidence_quote": first_sentence(c.source_text),
            "is_claim": False,
            "new_entities": [],
            "capabilities": [],
            "entities": [{"slug": "sonatype-firewall", "role": "primary"}],
        },
        verify_signal=lambda c: (
            {"verdict": "supported", "notes": "ok", "unsupported_claims": []}
            if c.thinking == "low"
            else {
                "verdict": "partial",
                "notes": "analysis",
                "unsupported_claims": ["It competes directly with JFrog Curation"],
            }
        ),
    )
    monkeypatch.setattr(runner, "complete", fake)
    monkeypatch.setattr(runner, "thinking_levels", lambda: ("off", "low"))
    before = _snapshot(factory)

    results = runner.compare(factory, runner.run_pass(factory))

    assert _snapshot(factory) == before  # nothing written to signals
    assert fake.count("triage") == 2 and fake.count("enrich_signal") == 2
    verify_calls = [c for c in fake.calls if c.operation == "verify_signal"]
    assert sorted(str(c.thinking) for c in verify_calls) == ["None", "None", "low", "low"]

    t = results["triage"]
    assert (t["tp"], t["fp"], t["fn"], t["tn"]) == (1, 1, 0, 0) and t["recall"] == 1.0
    assert [d["id"] for d in t["disagreements"]] == [llm]
    assert results["category"]["confusion"] == {"product_launch → feature_update": 1}
    assert results["impact"]["mean_signed_error"] == 1.0  # 5 vs 4: over-rates
    assert results["entity"]["strict"] == 1.0  # source company + the confirmed product
    assert results["quotes"]["verified"] == 1.0
    assert results["verifier"]["analysis_only_partials"] == 2  # configured (off) = partial
    assert results["thinking"]["matrix"]["agreement"] == 0.0
    assert results["thinking"]["alternative"] == "low"

    with factory() as s:
        runs = s.scalars(select(PipelineRun).order_by(PipelineRun.id)).all()
    assert [r.counts["kind"] for r in runs] == ["eval_pipeline", "eval_verify_low"]
    item = next(i for i in results["items"] if i["id"] == firewall)
    assert "summary" not in item["pipeline"] and "quote" not in item["pipeline"]


def test_pair_population_is_cross_source_within_window_and_band(collect_db):
    with collect_db.begin() as s:
        a, b = source(s, "A"), source(s, "B")
        x = signal(s, a, "x", embedding=vec(1.0))
        same_src = signal(s, a, "y", embedding=vec(0.8))  # same source: never a pair
        near = signal(s, b, "z", embedding=vec(0.8), days=3)
        far = signal(s, b, "w", embedding=vec(0.8), days=30)  # outside ±14 days
        signal(s, b, "v", embedding=vec(0.97), days=1)  # ≥ 0.95: outside the sample range
        ids = {x.id, same_src.id, near.id, far.id}
    with collect_db() as s:
        pop = pr.population(s, window_days=14, model=MODEL)
    found = {(p.a_id, p.b_id): round(p.similarity, 3) for p in pop}
    assert (x.id, near.id) in found and found[(x.id, near.id)] == pytest.approx(0.8, abs=1e-3)
    assert all(not ({k[0], k[1]} <= {x.id, same_src.id}) for k in found)
    assert all(far.id not in k for k in found if x.id in k)
    assert ids
