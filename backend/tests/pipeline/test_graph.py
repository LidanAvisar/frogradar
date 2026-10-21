"""LangGraph pipeline wiring: node order, one run row, skip, failures, budget, end-to-end."""

from typing import Any

import pytest
from sqlalchemy import func, select

from app.db.enums import RunStatus
from app.db.models import PipelineRun, Signal
from app.pipeline.graph import NODES, STEPS, run_pipeline
from tests.pipeline.factories import (
    FakeEmbed,
    FakeLLM,
    ctx,
    ensure_capability,
    entity,
    signal,
    source,
)

pytestmark = pytest.mark.db


def _recording_steps(order: list[str], **overrides: Any) -> dict[str, Any]:
    def make(name):
        def step(session, c):
            order.append(name)
            if name in overrides:
                return overrides[name](session, c)
            return {"done": 1}

        return step

    return {name: make(name) for name in NODES}


def _run(collect_db, steps, **kw):
    return run_pipeline(
        collect_db,
        steps=steps,
        make_ctx=lambda run_id: ctx(llm=FakeLLM(), factory=collect_db, run_id=run_id),
        **kw,
    )


def test_nodes_run_in_order_into_one_run_row(collect_db):
    order: list[str] = []
    run = _run(collect_db, _recording_steps(order))

    assert order == list(NODES) == [
        "collect", "embed", "dedup", "link", "triage", "enrich", "verify", "score",
        "brief", "battlecards",
    ]  # fmt: skip
    assert run.status == RunStatus.SUCCEEDED
    assert all(run.counts[n] == {"done": 1} for n in NODES)
    with collect_db() as s:
        assert s.scalar(select(func.count()).select_from(PipelineRun)) == 1


def test_skip(collect_db):
    order: list[str] = []
    run = _run(collect_db, _recording_steps(order), skip=frozenset({"collect"}))
    assert order == list(NODES[1:])
    assert run.counts["collect"] == {"skipped": "--skip"}
    assert run.counts["skip"] == ["collect"]


def test_failing_node_is_recorded_and_later_nodes_run(collect_db):
    def boom(session, c):
        raise ConnectionError("feeds unreachable")

    order: list[str] = []
    run = _run(collect_db, _recording_steps(order, collect=boom))

    assert order == list(NODES)
    assert run.status == RunStatus.PARTIAL
    assert run.counts["collect"]["error"] == "ConnectionError: feeds unreachable"
    assert "collect: ConnectionError" in run.error


def test_budget_stop_skips_remaining_llm_nodes(collect_db):
    order: list[str] = []
    steps = _recording_steps(order, triage=lambda s, c: {"stopped": "budget: reached"})
    run = _run(collect_db, steps)

    assert order == ["collect", "embed", "dedup", "link", "triage", "score"]
    assert run.counts["enrich"] == {"skipped": "budget exhausted"}
    assert run.status == RunStatus.PARTIAL


def test_failed_signal_makes_the_run_partial(collect_db):
    steps = _recording_steps([], enrich=lambda s, c: {"selected": 3, "enriched": 2, "failed": 1})
    assert _run(collect_db, steps).status == RunStatus.PARTIAL


def test_end_to_end_with_real_steps(collect_db):
    """All real steps except collect (network), with fake embeddings and a fake LLM."""
    with collect_db.begin() as s:
        ensure_capability(s)
        sonatype = entity(s, "sonatype", "Sonatype")
        blog = source(s, "Sonatype Blog", trust=0.9, entity=sonatype)
        ids = [
            signal(s, blog, f"Nexus release {i}", text=f"Sonatype shipped Nexus {i}. Details.").id
            for i in range(3)
        ]
    llm = FakeLLM()
    run = run_pipeline(
        collect_db,
        skip=frozenset({"collect"}),
        make_ctx=lambda run_id: ctx(FakeEmbed(), llm=llm, factory=collect_db, run_id=run_id),
    )

    assert run.status == RunStatus.SUCCEEDED, run.counts
    assert run.counts["embed"]["embedded"] == 3
    assert run.counts["triage"]["relevant"] == 3
    assert run.counts["enrich"]["enriched"] == 3
    assert run.counts["verify"]["supported"] == 3
    assert run.counts["score"]["scored"] == 3
    with collect_db() as s:
        sigs = s.scalars(select(Signal).where(Signal.id.in_(ids))).all()
        assert all(x.confidence == pytest.approx(0.825) for x in sigs)
        assert all(x.meta["score"]["band"] == "high" for x in sigs)

    # Second run: nothing new to do, no LLM calls.
    calls = len(llm.calls)
    again = run_pipeline(
        collect_db,
        skip=frozenset({"collect"}),
        make_ctx=lambda run_id: ctx(FakeEmbed(), llm=llm, factory=collect_db, run_id=run_id),
    )
    assert len(llm.calls) == calls
    assert again.counts["score"]["changed"] == 0


def test_steps_registry_matches_nodes():
    assert set(STEPS) == set(NODES)


def test_pipeline_report_sections(collect_db):
    import io

    from app.pipeline.pipeline_report import print_report

    with collect_db.begin() as s:
        ensure_capability(s)
        blog = source(s, "Blog", trust=0.9, entity=entity(s, "sonatype", "Sonatype"))
        signal(s, blog, "Registry news", text="Sonatype shipped a registry. More text here.")
        signal(s, blog, "Model prices", text="An LLM got cheaper. Nothing about registries.")

    def triage(call):
        if "prices" in call.title:
            return {"relevant": False, "reason": "LLM pricing only", "segments": []}
        return {"relevant": True, "reason": "registry", "segments": ["artifact_management"]}

    run = run_pipeline(
        collect_db,
        skip=frozenset({"collect"}),
        make_ctx=lambda run_id: ctx(
            FakeEmbed(), llm=FakeLLM(triage=triage), factory=collect_db, run_id=run_id
        ),
    )
    out = io.StringIO()
    with collect_db() as s:
        print_report(s, run.id, out)
    text = out.getvalue()
    assert run.status == "succeeded", run.counts
    assert f"Pipeline run {run.id}" in text
    assert "Triage: 1 relevant · 1 irrelevant" in text
    assert "LLM pricing only" in text  # every irrelevant signal is listed with its reason
    for section in ("Category", "Impact", "Verification", "Confidence band", "Evidence quotes"):
        assert section in text
    assert "enriched signals in full" in text and "why JFrog cares" in text
