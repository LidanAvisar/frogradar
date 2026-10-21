"""Run pipeline steps from the command line, one pipeline_runs row per invocation.

    python -m app.pipeline.run embed            # or: dedup, link; several: embed dedup link
    python -m app.pipeline.run report           # dedup calibration report (no run row, no LLM)

Each step is `step(session, ctx) -> counts`; counts land in pipeline_runs.counts[step].
Steps run in order, each in its own transaction; a failing step stops the run.
"""

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.llm import llm_context
from app.db.enums import RunStatus, RunTrigger
from app.db.models import LLMCall, PipelineRun
from app.pipeline.context import RunContext
from app.pipeline.dedup import dedup_signals
from app.pipeline.embed import embed_signals
from app.pipeline.link import link_signals

Step = Callable[[Session, RunContext], dict[str, Any]]
STEPS: dict[str, Step] = {"embed": embed_signals, "dedup": dedup_signals, "link": link_signals}

logger = logging.getLogger(__name__)


def run_steps(
    factory: sessionmaker[Session],
    names: list[str],
    *,
    trigger: RunTrigger = RunTrigger.MANUAL,
    make_ctx: Callable[[int], RunContext] = RunContext.create,
) -> PipelineRun:
    with factory.begin() as s:
        run = PipelineRun(trigger=trigger, counts={"steps": names})
        s.add(run)
        s.flush()
        run_id = run.id
    ctx = make_ctx(run_id)
    counts: dict[str, Any] = {"steps": names}
    status, error = RunStatus.SUCCEEDED, None
    with llm_context(run_id=run_id):
        for name in names:
            try:
                with factory.begin() as s:
                    counts[name] = STEPS[name](s, ctx)
            except Exception as e:  # noqa: BLE001 - recorded on the run; the CLI exits non-zero
                logger.exception("step %s failed", name)
                status, error = RunStatus.FAILED, f"{name}: {type(e).__name__}: {e}"[:500]
                break
    with factory.begin() as s:
        run = s.get_one(PipelineRun, run_id)
        cost = s.scalar(select(func.sum(LLMCall.cost_usd)).where(LLMCall.run_id == run_id))
        run.counts, run.status, run.error = counts, status, error
        run.cost_usd = cost or Decimal(0)
        run.finished_at = datetime.now(UTC)
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline.run")
    parser.add_argument("steps", nargs="+", choices=[*STEPS, "report"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from app.db.session import get_engine

    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    if args.steps == ["report"]:
        from app.pipeline.report import print_report

        with factory() as s:
            print_report(s, RunContext.create(None))
        return 0
    if "report" in args.steps:
        parser.error("run `report` on its own")

    run = run_steps(factory, args.steps)
    for name in args.steps:
        if name in run.counts:
            print(f"{name}: {run.counts[name]}")
    print(f"run {run.id}: {run.status} – cost ${run.cost_usd:.6f}")
    if run.error:
        print(f"error: {run.error}", file=sys.stderr)
    return 0 if run.status == RunStatus.SUCCEEDED else 1


if __name__ == "__main__":
    sys.exit(main())
