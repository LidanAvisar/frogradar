"""The daily pipeline as a LangGraph graph:

    collect → embed → dedup → link → triage → enrich → verify → score

    python -m app.pipeline.graph                         # everything (make pipeline)
    python -m app.pipeline.graph --skip collect          # process what is already stored
    python -m app.pipeline.graph --force enrich,verify   # redo despite current prompt versions

One pipeline_runs row per run; counts per node. Nodes reuse the step functions
(`step(session, ctx) -> counts`). A failing node is recorded and the graph goes on (a collect
failure must not block enrichment of stored signals); a failing signal is counted inside its
node. BudgetExceeded skips the remaining LLM nodes. Status: succeeded, or partial when any node
or signal failed or a stage stopped early; failed only if the graph itself crashed.
"""

import argparse
import logging
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.llm import BudgetExceeded, llm_context
from app.db.enums import RunStatus, RunTrigger
from app.db.models import LLMCall, PipelineRun
from app.pipeline.collect import collect_all, failed_sources_summary
from app.pipeline.collectors import PoliteClient
from app.pipeline.context import RunContext
from app.pipeline.dedup import dedup_signals
from app.pipeline.embed import embed_signals
from app.pipeline.enrich import enrich_signals
from app.pipeline.link import link_signals
from app.pipeline.score import score_signals
from app.pipeline.triage import triage_signals
from app.pipeline.verify import verify_signals

logger = logging.getLogger(__name__)

Step = Callable[[Session, RunContext], dict[str, Any]]
NODES = ("collect", "embed", "dedup", "link", "triage", "enrich", "verify", "score")
LLM_NODES = frozenset({"embed", "triage", "enrich", "verify"})
FORCEABLE = frozenset({"triage", "enrich", "verify"})


def collect_step(session: Session, ctx: RunContext) -> dict[str, Any]:
    """Collect all active sources into this run (sources fail one by one, not the node)."""
    if ctx.session_factory is None or ctx.run_id is None:
        raise RuntimeError("collect needs ctx.session_factory and ctx.run_id")
    settings = get_settings()
    with PoliteClient(settings) as client:
        report = collect_all(
            session_factory=ctx.session_factory,
            client=client,
            settings=settings,
            run_id=ctx.run_id,
        )
        requests = client.request_count
    counts: dict[str, Any] = {"status": report.status.value, **report.totals()}
    counts["http_requests"] = requests
    if error := failed_sources_summary(report):
        counts["failed_sources"] = error
    return counts


STEPS: dict[str, Step] = {
    "collect": collect_step,
    "embed": embed_signals,
    "dedup": dedup_signals,
    "link": link_signals,
    "triage": triage_signals,
    "enrich": enrich_signals,
    "verify": verify_signals,
    "score": score_signals,
}


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {**a, **b}


class PipelineState(TypedDict, total=False):
    counts: Annotated[dict[str, Any], _merge]
    budget_exhausted: bool


def _node(
    name: str,
    step: Step,
    ctx: RunContext,
    factory: sessionmaker[Session],
    skip: frozenset[str],
) -> Callable[[PipelineState], PipelineState]:
    def node(state: PipelineState) -> PipelineState:
        if name in skip:
            return {"counts": {name: {"skipped": "--skip"}}}
        if name in LLM_NODES and state.get("budget_exhausted"):
            return {"counts": {name: {"skipped": "budget exhausted"}}}
        logger.info("node %s: start", name)
        try:
            with factory.begin() as s:
                counts = step(s, ctx)
        except BudgetExceeded as e:
            logger.warning("node %s: budget exhausted: %s", name, e)
            return {"counts": {name: {"error": f"budget: {e}"[:300]}}, "budget_exhausted": True}
        except Exception as e:  # noqa: BLE001 - recorded; later nodes still run
            logger.exception("node %s failed", name)
            return {"counts": {name: {"error": f"{type(e).__name__}: {e}"[:500]}}}
        update: PipelineState = {"counts": {name: counts}}
        if counts.get("stopped"):
            update["budget_exhausted"] = True
        return update

    return node


def build_graph(
    ctx: RunContext,
    factory: sessionmaker[Session],
    *,
    skip: frozenset[str] = frozenset(),
    steps: Mapping[str, Step] = STEPS,
) -> Any:
    graph = StateGraph(PipelineState)
    for name in NODES:
        graph.add_node(name, _node(name, steps[name], ctx, factory, skip))
    graph.add_edge(START, NODES[0])
    for a, b in zip(NODES, NODES[1:], strict=False):
        graph.add_edge(a, b)
    graph.add_edge(NODES[-1], END)
    return graph.compile()


def run_status(counts: Mapping[str, Any]) -> RunStatus:
    for node in counts.values():
        if not isinstance(node, dict):
            continue
        if node.get("error") or node.get("failed") or node.get("stopped"):
            return RunStatus.PARTIAL
        if node.get("status") in (RunStatus.PARTIAL.value, RunStatus.FAILED.value):
            return RunStatus.PARTIAL
    return RunStatus.SUCCEEDED


def run_pipeline(
    factory: sessionmaker[Session],
    *,
    skip: frozenset[str] = frozenset(),
    force: frozenset[str] = frozenset(),
    trigger: RunTrigger = RunTrigger.MANUAL,
    make_ctx: Callable[[int], RunContext] | None = None,
    steps: Mapping[str, Step] = STEPS,
) -> PipelineRun:
    with factory.begin() as s:
        run = PipelineRun(trigger=trigger, counts={"graph": list(NODES)})
        s.add(run)
        s.flush()
        run_id = run.id
    ctx = (
        make_ctx(run_id)
        if make_ctx
        else RunContext.create(run_id, session_factory=factory, force=force)
    )
    status, error = RunStatus.FAILED, None
    counts: dict[str, Any] = {}
    try:
        with llm_context(run_id=run_id):
            final = build_graph(ctx, factory, skip=skip, steps=steps).invoke({"counts": {}})
        counts = final.get("counts", {})
        status = run_status(counts)
        errors = [f"{n}: {c['error']}" for n, c in counts.items() if c.get("error")]
        error = "; ".join(errors)[:4000] or None
    except Exception as e:  # noqa: BLE001 - the run row must record the crash
        logger.exception("pipeline crashed")
        error = f"{type(e).__name__}: {e}"[:4000]
    with factory.begin() as s:
        run = s.get_one(PipelineRun, run_id)
        cost = s.scalar(select(func.sum(LLMCall.cost_usd)).where(LLMCall.run_id == run_id))
        run.counts = {"graph": list(NODES), "skip": sorted(skip), "force": sorted(force), **counts}
        run.status, run.error = status, error
        run.cost_usd = cost or Decimal(0)
        run.finished_at = datetime.now(UTC)
    return run


def _names(value: str, allowed: frozenset[str] | tuple[str, ...]) -> frozenset[str]:
    names = frozenset(v.strip() for v in value.split(",") if v.strip())
    unknown = names - set(allowed)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown: {sorted(unknown)} (allowed: {sorted(allowed)})")
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline.graph")
    parser.add_argument("--skip", type=lambda v: _names(v, NODES), default=frozenset())
    parser.add_argument("--force", type=lambda v: _names(v, FORCEABLE), default=frozenset())
    parser.add_argument("--scheduled", action="store_true", help="mark the run as scheduled")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from app.db.session import get_engine

    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    run = run_pipeline(
        factory,
        skip=args.skip,
        force=args.force,
        trigger=RunTrigger.SCHEDULED if args.scheduled else RunTrigger.MANUAL,
    )
    for name in NODES:
        print(f"{name:>8}: {run.counts.get(name)}")
    print(f"run {run.id}: {run.status} – cost ${run.cost_usd:.6f}")
    if run.error:
        print(f"error: {run.error}", file=sys.stderr)
    return 0 if run.status != RunStatus.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
