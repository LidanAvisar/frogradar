"""Daily scheduler (D42): cron parsing, job wiring, the cross-process lock. Nothing is started."""

import sys

import pytest
from sqlalchemy import func, select, text

from app.db.enums import RunTrigger
from app.db.models import PipelineRun
from app.pipeline.graph import run_pipeline
from app.pipeline.lock import PipelineLocked, pipeline_lock
from app.scheduler import JOB_ID, build_scheduler, daily_job, parse_cron


def test_cron_parsing():
    trigger = parse_cron("30 5 * * 1-5")
    assert str(trigger.timezone) == "UTC"
    assert {f.name: str(f) for f in trigger.fields}["hour"] == "5"
    with pytest.raises(ValueError):
        parse_cron("every day at six")


def test_scheduler_is_built_but_never_started():
    scheduler = build_scheduler("0 6 * * *", lambda: None)
    job = scheduler.get_job(JOB_ID)
    assert job.max_instances == 1 and job.coalesce is True
    assert scheduler.running is False


def test_importing_the_api_does_not_load_the_scheduler():
    import app.main  # noqa: F401

    loaded = "app.scheduler" in sys.modules  # loaded by this test module itself
    assert loaded and "scheduler" not in vars(app.main)


def test_daily_job_outcomes():
    calls = []

    def ok(factory, *, trigger):
        calls.append(trigger)
        return type("Run", (), {"id": 7, "status": "succeeded", "cost_usd": 0})()

    def locked(factory, *, trigger):
        raise PipelineLocked("busy")

    def boom(factory, *, trigger):
        raise RuntimeError("crash")

    assert daily_job(None, run=ok) == "ran" and calls == [RunTrigger.SCHEDULED]
    assert daily_job(None, run=locked) == "locked"
    assert daily_job(None, run=boom) == "failed"  # the scheduler loop survives


def test_lock_blocks_a_second_holder_and_is_released(collect_engine):
    with pipeline_lock(collect_engine), pytest.raises(PipelineLocked):  # noqa: SIM117
        with pipeline_lock(collect_engine):  # the inner one must fail
            pass
    with pipeline_lock(collect_engine):  # released: can be taken again
        pass


def test_run_pipeline_refuses_to_overlap(collect_db, collect_engine):
    with pipeline_lock(collect_engine), pytest.raises(PipelineLocked):
        run_pipeline(collect_db, steps={})
    with collect_db() as s:
        assert s.scalar(select(func.count()).select_from(PipelineRun)) == 0  # no run row


def test_lock_is_released_when_the_holder_dies(collect_engine):
    conn = collect_engine.connect()

    assert conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": 0x46524F47})
    conn.invalidate()  # connection dropped (process crash): Postgres frees the lock
    with pipeline_lock(collect_engine):
        pass
