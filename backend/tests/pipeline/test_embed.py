"""Embed step with a fake embed function (no network) and the CLI run wrapper."""

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from app.db.enums import RunStatus
from app.db.models import PipelineRun, Signal
from app.pipeline.embed import embed_signals, embedding_input
from app.pipeline.run import run_steps
from tests.pipeline.factories import FakeEmbed, ctx, rotated, signal, source

pytestmark = pytest.mark.db


def test_embedding_input_truncates_raw_text():
    assert embedding_input(" Title ", "abcdef", 3) == "Title\n\nabc"


def test_embeds_missing_in_batches_and_is_idempotent(db_session):
    s = db_session
    src = source(s, "A")
    sigs = [signal(s, src, f"t{i}", text="x" * 5000) for i in range(5)]
    fake = FakeEmbed()

    counts = embed_signals(s, ctx(fake, batch_size=2, max_chars=300))

    assert counts == {"candidates": 5, "embedded": 5, "re_embedded": 0, "batches": 3}
    assert [len(c) for c in fake.calls] == [2, 2, 1]
    assert all(len(t) == len("t0\n\n") + 300 for c in fake.calls for t in c)
    s.expire_all()
    assert all(s.get(Signal, x.id).embedding_model == fake.model for x in sigs)

    assert embed_signals(s, ctx(fake))["candidates"] == 0
    assert len(fake.calls) == 3  # no new calls


def test_model_change_re_embeds_and_resets_cluster(db_session):
    s = db_session
    src = source(s, "A")
    canonical = signal(s, src, embedding=rotated(0), model="old/model")
    dup = signal(s, src, embedding=rotated(1), model="new/model")
    canonical.dedup_cluster_id = canonical.id
    dup.dedup_cluster_id = canonical.id
    s.flush()

    counts = embed_signals(s, ctx(FakeEmbed(model="new/model"), model="new/model"))

    assert counts["candidates"] == 1 and counts["re_embedded"] == 1
    s.expire_all()
    assert s.get(Signal, canonical.id).embedding_model == "new/model"
    # Its cluster is clustered again by the next dedup run.
    assert [s.get(Signal, x.id).dedup_cluster_id for x in (canonical, dup)] == [None, None]


def test_wrong_model_from_gateway_fails(db_session):
    s = db_session
    signal(s, source(s, "A"))
    with pytest.raises(RuntimeError, match="expected"):
        embed_signals(s, ctx(FakeEmbed(model="other/model")))


def test_run_steps_records_counts(collect_engine: Engine, collect_db: sessionmaker):
    with collect_db.begin() as s:
        src = source(s, "A")
        signal(s, src, "one")
        signal(s, src, "two", days=1)

    run = run_steps(collect_db, ["embed", "dedup", "link"], make_ctx=lambda _id: ctx())

    assert run.status == RunStatus.SUCCEEDED
    assert run.counts["embed"]["embedded"] == 2
    assert run.counts["dedup"]["clusters"] == 2
    assert run.counts["link"]["checked"] == 2
    with collect_db() as s:
        stored = s.scalar(select(PipelineRun).where(PipelineRun.id == run.id))
        assert stored.counts["steps"] == ["embed", "dedup", "link"] and stored.finished_at


def test_failed_step_marks_run_failed(collect_db: sessionmaker):
    with collect_db.begin() as s:
        signal(s, source(s, "A"))

    run = run_steps(
        collect_db, ["embed", "dedup"], make_ctx=lambda _id: ctx(FakeEmbed(model="other/model"))
    )

    assert run.status == RunStatus.FAILED and "embed" in run.error
    assert "dedup" not in run.counts
