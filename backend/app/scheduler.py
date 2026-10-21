"""Daily scheduler (docker-compose service `scheduler`): runs the full pipeline graph on a cron.

    python -m app.scheduler            # blocks; cron from DAILY_RUN_CRON (UTC), default 0 6 * * *

- One job, max_instances=1 and coalesce: missed runs (container down) collapse into one run
  when it comes back, within misfire_grace_time.
- Overlap across processes (a manual `make pipeline`, a backfill) is prevented by the pipeline's
  advisory lock (app.pipeline.lock): a locked run is skipped and logged, not queued.
- The daily LLM budget (LLM_DAILY_BUDGET_USD) is enforced by the gateway: once reached, the run's
  remaining LLM nodes are skipped and the run is marked partial.
- Nothing here runs on import. Tests build the scheduler without starting it.
"""

import contextlib
import logging
import sys
from collections.abc import Callable
from datetime import UTC

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.enums import RunTrigger
from app.pipeline.lock import PipelineLocked

logger = logging.getLogger("app.scheduler")

JOB_ID = "daily_pipeline"
MISFIRE_GRACE_S = 3 * 3600


def parse_cron(expr: str) -> CronTrigger:
    """5-field crontab → trigger in UTC. ValueError on a bad expression."""
    return CronTrigger.from_crontab(expr.strip(), timezone=UTC)


def daily_job(factory: sessionmaker[Session], run: Callable[..., object] | None = None) -> str:
    """One scheduled run. Returns "ran", "locked" or "failed" (never raises: keep the loop)."""
    if run is None:
        from app.pipeline.graph import run_pipeline as run
    try:
        result = run(factory, trigger=RunTrigger.SCHEDULED)
    except PipelineLocked:
        logger.warning("daily run skipped: another pipeline run is in progress")
        return "locked"
    except Exception:  # noqa: BLE001 - the scheduler must survive a crashed run
        logger.exception("daily run crashed")
        return "failed"
    logger.info(
        "daily run %s: %s, cost $%s",
        getattr(result, "id", "?"),
        getattr(result, "status", "?"),
        getattr(result, "cost_usd", "?"),
    )
    return "ran"


def build_scheduler(cron: str, job: Callable[[], object]) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=UTC)
    scheduler.add_job(
        job,
        parse_cron(cron),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_S,
    )
    return scheduler


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from app.db.session import get_engine

    cron = get_settings().daily_run_cron
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    try:
        scheduler = build_scheduler(cron, lambda: daily_job(factory))
    except ValueError as e:
        logger.error("invalid DAILY_RUN_CRON %r: %s", cron, e)
        return 2
    logger.info("scheduler started: %r (UTC), next run %s", cron, scheduler.get_job(JOB_ID))
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        scheduler.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
