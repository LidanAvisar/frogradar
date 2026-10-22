"""A simple in-process worker: one background thread runs missions one at a time (D71).

The API enqueues (mission_id, phase); phase "plan" writes the plan, phase "research" runs the
approved plan. On startup, missions left in planning / running by a crash or restart are marked
failed with a reason; queued ones are enqueued again.
"""

import logging
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.mission.runner import MissionRunner, Tools
from app.agents.mission.web import page_fetcher, tavily_search
from app.core.catalog import load_pipeline
from app.core.config import get_settings
from app.db.enums import MissionStatus
from app.db.models import Mission

logger = logging.getLogger(__name__)

RESTART_REASON = "Interrupted by a server restart while {status}; start a new mission to retry."


def recover(session: Session, now: datetime) -> list[tuple[int, str]]:
    """Fail missions stuck in planning / running; return queued ones to enqueue again."""
    requeue: list[tuple[int, str]] = []
    for m in session.scalars(
        select(Mission)
        .where(
            Mission.status.in_(
                (MissionStatus.PLANNING, MissionStatus.RUNNING, MissionStatus.QUEUED)
            )
        )
        .order_by(Mission.id)
    ):
        if m.status == MissionStatus.QUEUED:
            requeue.append((m.id, "research" if (m.plan or {}).get("approved") else "plan"))
            continue
        m.error = RESTART_REASON.format(status=m.status.value)
        m.status = MissionStatus.FAILED
        m.finished_at = now
    session.commit()
    return requeue


def run_job(session: Session, mission_id: int, phase: str, tools: Tools) -> None:
    runner = MissionRunner(session, mission_id, tools)
    m = session.get(Mission, mission_id)
    if m is None or m.status != MissionStatus.QUEUED:
        return  # stopped or already handled
    if phase == "plan":
        runner.plan()
    else:
        runner.research()


def production_tools() -> tuple[Tools, Callable[[], None]]:
    from app.core.llm import complete, embed

    settings = get_settings()
    if settings.demo_mode:  # defense in depth: no web search or page fetches in demo mode
        return Tools(complete=complete, embed=embed), lambda: None
    cfg = load_pipeline().missions
    fetch, close = page_fetcher(settings, cfg)
    tools = Tools(
        complete=complete,
        embed=lambda texts: embed(texts, operation="mission_embed"),
        search=tavily_search(settings) if settings.tavily_api_key else None,
        fetch=fetch,
    )
    return tools, close


class MissionWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tools_factory: Callable[[], tuple[Tools, Callable[[], None]]] = production_tools,
    ) -> None:
        self.session_factory = session_factory
        self.tools_factory = tools_factory
        self._queue: queue.Queue[tuple[int, str] | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def submit(self, mission_id: int, phase: str) -> None:
        self._queue.put((mission_id, phase))

    def start(self) -> None:
        with self.session_factory() as s:
            jobs = recover(s, datetime.now(UTC))
        for job in jobs:
            self.submit(*job)
        self._thread = threading.Thread(target=self._loop, name="mission-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)

    def _loop(self) -> None:
        while (job := self._queue.get()) is not None:
            mission_id, phase = job
            try:
                tools, close = self.tools_factory()
                try:
                    with self.session_factory() as s:
                        run_job(s, mission_id, phase, tools)
                finally:
                    close()
            except Exception:  # noqa: BLE001 - the worker must survive one bad mission
                logger.exception("mission %s (%s) crashed in the worker", mission_id, phase)


_worker: MissionWorker | None = None


def get_worker() -> MissionWorker:
    global _worker  # noqa: PLW0603
    if _worker is None:
        from app.db.session import get_engine

        _worker = MissionWorker(sessionmaker(bind=get_engine(), expire_on_commit=False))
    return _worker
