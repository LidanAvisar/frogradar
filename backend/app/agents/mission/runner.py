"""The research mission agent (S15, D71-D73).

Two LangGraph runs per mission, with the analyst in between:

    plan graph:      plan → (status awaiting_approval; the analyst approves or edits the plan)
    research graph:  for each sub-question: search → read → extract → verify;  then report

- search: FrogRadar's own knowledge (app.rag.retrieve, signals only) + Tavily web search
- read: fetch the top web results with the polite client (robots.txt, rate limit, SSRF guard,
  text/html only, trafilatura); FrogRadar signals are read from their stored analysis
- extract (bulk model): findings, each citing one source with an exact quote (checked in code)
- verify (verify model, a different provider): each finding against its cited excerpt; only
  supported and partial findings are kept ("partial" is shown as "Partly verified", confidence at
  most medium); unsupported ones are dropped and recorded
- report (synthesis model): summary points citing kept findings only, plus open questions

Every node writes one mission_steps row (kind, short summary, cost). Hard limits, checked before
every counted step: the mission's budget (also enforced by the gateway: MissionBudgetExceeded),
max_steps (search / read / extract / verify rows), the wall-clock timeout and the analyst's Stop.
When a limit hits, the run jumps to the report, which then needs no LLM call (it lists the
findings so far and the unanswered sub-questions).

Page text is untrusted: it is always inside a hash-tagged delimiter (app.pipeline.prompting.delimit)
and every prompt says never to follow instructions inside it.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.mission.schemas import Extraction, MissionPlan, MissionReport, Verdicts
from app.agents.mission.web import FetchFn, SearchFn, SkipPage, UnsafeUrl, time_range
from app.core.catalog import MissionSettings, load_pipeline
from app.core.llm import MissionBudgetExceeded, llm_context, load_prompt
from app.core.llm.errors import BudgetExceeded
from app.core.llm.ledger import mission_spend
from app.db.enums import (
    FindingConfidence,
    FindingStatus,
    MissionSourceKind,
    MissionStatus,
    MissionStepKind,
    SignalCategory,
    Verification,
)
from app.db.models import Entity, Mission, MissionFinding, MissionSource, MissionStep
from app.pipeline.collectors.http import FetchError, RobotsDisallowed
from app.pipeline.context import CompleteFn, EmbedFn
from app.pipeline.normalize import canonical_url
from app.pipeline.prompting import Catalog, delimit
from app.pipeline.quotes import quote_in_text
from app.rag.filters import Filters
from app.rag.retrieve import retrieve

logger = logging.getLogger(__name__)

COUNTED_KINDS = (
    MissionStepKind.SEARCH,
    MissionStepKind.READ,
    MissionStepKind.ANALYZE,
    MissionStepKind.VERIFY,
)
PHASES = ("search", "read", "extract", "verify")
_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Tools:
    """Everything the agent calls outside the DB; tests inject fakes."""

    complete: CompleteFn
    embed: EmbedFn
    search: SearchFn | None = None
    fetch: FetchFn | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


class Stop(Exception):  # noqa: N818 - control flow, not an error
    def __init__(self, status: MissionStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class State(TypedDict):
    idx: int
    phase: str
    stop_status: str | None
    stop_reason: str | None


@dataclass
class _QuestionWork:
    kb_docs: list[Any] = field(default_factory=list)
    web: list[Any] = field(default_factory=list)
    sources: list[MissionSource] = field(default_factory=list)
    not_found: str = ""
    answered: bool = False


def _clip(text: str, n: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _label(v: Verification) -> str:
    return "partly verified" if v == Verification.PARTIAL else "verified"


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).netloc.removeprefix("www.")


class MissionRunner:
    def __init__(
        self,
        session: Session,
        mission_id: int,
        tools: Tools,
        cfg: MissionSettings | None = None,
    ) -> None:
        self.s = session
        self.mission_id = mission_id
        self.tools = tools
        self.cfg = cfg or load_pipeline().missions
        self._spend_mark = Decimal(0)
        self._work: dict[int, _QuestionWork] = {}
        self._deadline: datetime | None = None

    @property
    def m(self) -> Mission:
        m = self.s.get(Mission, self.mission_id)
        if m is None:
            raise LookupError(f"mission {self.mission_id} not found")
        return m

    # ---------- shared helpers ----------

    def _spent(self) -> Decimal:
        return mission_spend(self.s, self.mission_id)

    def _step(
        self,
        kind: MissionStepKind,
        summary: str,
        *,
        input: dict[str, Any] | None = None,  # noqa: A002
        output: dict[str, Any] | None = None,
    ) -> MissionStep:
        spent = self._spent()
        no = (
            self.s.scalar(
                select(func.coalesce(func.max(MissionStep.step_no), 0)).where(
                    MissionStep.mission_id == self.mission_id
                )
            )
            or 0
        ) + 1
        step = MissionStep(
            mission_id=self.mission_id,
            step_no=no,
            kind=kind,
            summary=_clip(summary, 300),
            input=input or {},
            output=output or {},
            cost_usd=max(spent - self._spend_mark, Decimal(0)),
        )
        self._spend_mark = spent
        self.s.add(step)
        m = self.m
        m.cost_usd = spent
        self.s.commit()
        return step

    def counted_steps(self) -> int:
        return (
            self.s.scalar(
                select(func.count()).where(
                    MissionStep.mission_id == self.mission_id, MissionStep.kind.in_(COUNTED_KINDS)
                )
            )
            or 0
        )

    def _guard(self) -> None:
        """Raise Stop if a hard limit is reached. Called before every counted step."""
        self.s.expire_all()
        m = self.m
        if m.stop_requested:
            raise Stop(MissionStatus.CANCELLED, "Stopped by the analyst.")
        spent = self._spent()
        if spent >= m.budget_usd:
            raise Stop(
                MissionStatus.BUDGET_EXCEEDED,
                f"Budget reached: ${spent:.4f} of ${m.budget_usd:.2f}.",
            )
        if self.counted_steps() >= m.max_steps:
            raise Stop(MissionStatus.COMPLETED, f"Step limit reached ({m.max_steps} steps).")
        if self._deadline is not None and self.tools.clock() >= self._deadline:
            raise Stop(MissionStatus.FAILED, f"Timed out after {m.timeout_s} s.")

    def _complete(self, task: str, prompt_name: str, schema: type, **vars: Any) -> Any:  # noqa: A002
        prompt = load_prompt(prompt_name)
        with llm_context(mission_id=self.mission_id):
            return self.tools.complete(
                task, prompt_name, prompt.messages(**vars), schema, prompt_version=prompt.id
            )

    def _portfolio(self) -> str:
        return Catalog.load(self.s).jfrog_portfolio

    def scope_text(self) -> str:
        m = self.m
        scope = m.scope or {}
        parts = []
        if names := scope.get("company_names"):
            parts.append("companies: " + ", ".join(names))
        if days := scope.get("days"):
            parts.append(f"last {days} days")
        return "; ".join(parts) or "no restriction"

    def _fail(self, status: MissionStatus, reason: str) -> None:
        self.s.rollback()
        m = self.m
        m.status = status
        m.error = _clip(reason, 500)
        m.finished_at = self.tools.clock()
        m.cost_usd = self._spent()
        self.s.commit()

    # ---------- plan ----------

    def plan(self) -> None:
        m = self.m
        m.status = MissionStatus.PLANNING
        self.s.commit()
        try:
            tag, text = delimit(m.brief, 4000)
            out: MissionPlan = self._complete(
                "synthesis",
                "mission_plan",
                MissionPlan,
                tag=tag,
                text=text,
                scope=self.scope_text(),
                jfrog_portfolio=self._portfolio(),
            )
        except BudgetExceeded as e:
            self._step(MissionStepKind.ERROR, f"Planning stopped: {e}")
            self._fail(MissionStatus.BUDGET_EXCEEDED, str(e))
            return
        except Exception as e:  # noqa: BLE001 - recorded on the mission
            logger.exception("mission %s: planning failed", self.mission_id)
            self._fail(MissionStatus.FAILED, f"Planning failed: {type(e).__name__}: {e}")
            return
        self.s.expire_all()
        m = self.m
        if m.stop_requested:
            m.status = MissionStatus.CANCELLED
            m.error = "Stopped by the analyst."
            m.finished_at = self.tools.clock()
        else:
            m.status = MissionStatus.AWAITING_APPROVAL
        m.title = out.title
        m.plan = {
            "sub_questions": [q.model_dump() for q in out.sub_questions],
            "approved": False,
            "prompt_version": load_prompt("mission_plan").id,
        }
        self._step(
            MissionStepKind.PLAN,
            f"Planned {len(out.sub_questions)} sub-questions · waiting for approval",
            output={"sub_questions": [q.question for q in out.sub_questions]},
        )

    # ---------- research graph ----------

    def questions(self) -> list[dict[str, Any]]:
        return list((self.m.plan or {}).get("sub_questions") or [])

    def research(self) -> None:
        m = self.m
        if not (m.plan or {}).get("approved"):
            raise RuntimeError("research needs an approved plan")
        now = self.tools.clock()
        m.status = MissionStatus.RUNNING
        m.started_at = now
        self._deadline = now + timedelta(seconds=m.timeout_s)
        self._spend_mark = self._spent()
        self.s.commit()
        graph = self._graph()
        try:
            graph.invoke(
                {"idx": 0, "phase": "search", "stop_status": None, "stop_reason": None},
                {"recursion_limit": 10 * len(self.questions()) + 20},
            )
        except Exception as e:  # noqa: BLE001 - recorded on the mission
            logger.exception("mission %s: research crashed", self.mission_id)
            self._fail(MissionStatus.FAILED, f"Research failed: {type(e).__name__}: {e}")

    def _graph(self) -> Any:
        g = StateGraph(State)
        for name in PHASES:
            g.add_node(name, self._node(name))
        g.add_node("report", self._report_node)
        g.add_edge(START, "search")
        for name in PHASES:
            g.add_conditional_edges(name, self._route, [*PHASES, "report"])
        g.add_edge("report", END)
        return g.compile()

    def _route(self, state: State) -> str:
        if state["stop_status"] is not None or state["idx"] >= len(self.questions()):
            return "report"
        return state["phase"]

    def _node(self, name: str) -> Callable[[State], dict[str, Any]]:
        fn = getattr(self, f"_{name}")

        def node(state: State) -> dict[str, Any]:
            idx = state["idx"]
            nxt = PHASES[(PHASES.index(name) + 1) % len(PHASES)]
            try:
                self._guard()
                fn(idx, self.questions()[idx])
            except Stop as e:
                return {"stop_status": e.status.value, "stop_reason": e.reason}
            except MissionBudgetExceeded as e:
                self.s.rollback()
                self._step(MissionStepKind.ERROR, f"Budget reached during {name}: {e}")
                return {
                    "stop_status": MissionStatus.BUDGET_EXCEEDED.value,
                    "stop_reason": f"Budget reached: {e}",
                }
            except BudgetExceeded as e:  # the daily LLM budget
                self.s.rollback()
                self._step(MissionStepKind.ERROR, f"Daily LLM budget reached during {name}")
                return {"stop_status": MissionStatus.FAILED.value, "stop_reason": str(e)}
            except Exception as e:  # noqa: BLE001 - one failing step is recorded, the run goes on
                logger.exception("mission %s: %s failed", self.mission_id, name)
                self.s.rollback()
                self._step(
                    MissionStepKind.ERROR,
                    f"{name.capitalize()} failed for sub-question {idx + 1}: {type(e).__name__}",
                    output={"error": _clip(str(e), 300)},
                )
            return {"idx": idx + 1 if nxt == "search" else idx, "phase": nxt}

        return node

    def _work_for(self, idx: int) -> _QuestionWork:
        return self._work.setdefault(idx, _QuestionWork())

    # search: knowledge base + web
    def _search(self, idx: int, q: dict[str, Any]) -> None:
        w = self._work_for(idx)
        m = self.m
        notes: list[str] = []
        scope = m.scope or {}
        if self.cfg.kb_results > 0:
            try:
                w.kb_docs = self._kb(q["question"], scope)
            except BudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001
                notes.append(f"knowledge base search failed ({type(e).__name__})")
        if self.cfg.web_results > 0 and self.tools.search is not None:
            query = q.get("search_query") or q["question"]
            try:
                w.web = self.tools.search(
                    query, self.cfg.web_results, time_range(scope.get("days"))
                )
            except FetchError as e:
                notes.append(f"web search skipped: {e}")
        elif self.tools.search is None:
            notes.append("web search not configured")
        summary = f"Q{idx + 1}: FrogRadar {len(w.kb_docs)} signals · web {len(w.web)} results" + (
            f" · {'; '.join(notes)}" if notes else ""
        )
        self._step(
            MissionStepKind.SEARCH,
            summary,
            input={"question": q["question"], "query": q.get("search_query")},
            output={
                "kb": [{"signal_id": d.signal_id, "title": _clip(d.title, 120)} for d in w.kb_docs],
                "web": [{"title": _clip(r.title, 120), "url": r.url} for r in w.web],
                "notes": notes,
            },
        )

    def _kb(self, question: str, scope: dict[str, Any]) -> list[Any]:
        ask = load_pipeline().ask
        k = self.cfg.kb_results
        cfg = ask.model_copy(
            update={"k": k, "max_docs": max(k, 1), "max_per_type": {"battlecard": 0, "radar": 0}}
        )
        entity_ids: list[int] = []
        for eid in scope.get("company_ids") or []:
            entity_ids.append(eid)
            entity_ids += list(self.s.scalars(select(Entity.id).where(Entity.parent_id == eid)))
        since = None
        if days := scope.get("days"):
            since = self.tools.clock() - timedelta(days=int(days))
        f = Filters(entity_ids=tuple(entity_ids), since=since)
        with llm_context(mission_id=self.mission_id):
            found = retrieve(self.s, question, f, embed=self.tools.embed, cfg=cfg)
        return [d for d in found.docs if d.type == "signal" and d.signal_id is not None][:k]

    def _source(self, kind: MissionSourceKind, url: str, title: str, excerpt: str,
                signal_id: int | None = None) -> MissionSource:  # fmt: skip
        canon = f"frogradar:signal:{signal_id}" if signal_id is not None else canonical_url(url)
        existing = self.s.scalar(
            select(MissionSource).where(
                MissionSource.mission_id == self.mission_id, MissionSource.canonical_url == canon
            )
        )
        if existing is not None:
            return existing
        src = MissionSource(
            mission_id=self.mission_id, kind=kind, url=url, canonical_url=canon,
            title=_clip(title or _host(url), 300), excerpt=excerpt, signal_id=signal_id,
        )  # fmt: skip
        self.s.add(src)
        self.s.flush()
        return src

    # read: kb signals (stored analysis) + fetched web pages
    def _read(self, idx: int, q: dict[str, Any]) -> None:
        w = self._work_for(idx)
        for d in w.kb_docs:
            w.sources.append(
                self._source(MissionSourceKind.KB, d.url, d.title, d.text, signal_id=d.signal_id)
            )
        read, skipped = [], []
        seen = {
            u
            for (u,) in self.s.execute(
                select(MissionSource.canonical_url).where(
                    MissionSource.mission_id == self.mission_id,
                    MissionSource.kind == MissionSourceKind.WEB,
                )
            )
        }
        for r in w.web:
            if len(read) >= self.cfg.pages_per_question or self.tools.fetch is None:
                break
            if canonical_url(r.url) in seen:
                src = self._source(MissionSourceKind.WEB, r.url, r.title, "")
                w.sources.append(src)
                read.append({"url": r.url, "title": src.title, "reused": True})
                continue
            try:
                page = self.tools.fetch(r.url)
            except UnsafeUrl as e:
                skipped.append({"url": r.url, "reason": f"blocked: {e}"})
                continue
            except RobotsDisallowed as e:
                skipped.append({"url": r.url, "reason": f"robots.txt: {e}"})
                continue
            except (SkipPage, FetchError) as e:
                skipped.append({"url": r.url, "reason": str(e)})
                continue
            src = self._source(MissionSourceKind.WEB, page.url, page.title or r.title, page.text)
            seen.add(src.canonical_url)
            w.sources.append(src)
            read.append({"url": page.url, "title": src.title})
        parts = [f"Q{idx + 1}: read {len(read)} pages"]
        if read:
            parts.append(", ".join(_host(x["url"]) for x in read))
        if w.kb_docs:
            parts.append(f"{len(w.kb_docs)} FrogRadar signals")
        if skipped:
            parts.append(f"skipped {len(skipped)} ({_clip(skipped[0]['reason'], 80)})")
        self._step(
            MissionStepKind.READ,
            " · ".join(parts),
            output={
                "read": read,
                "skipped": [{**x, "reason": _clip(x["reason"], 200)} for x in skipped],
            },
        )

    # extract: cited findings from this question's sources
    def _extract(self, idx: int, q: dict[str, Any]) -> None:
        w = self._work_for(idx)
        sources = [s for s in w.sources if s.excerpt]
        if not sources:
            w.not_found = "No sources could be read for this sub-question."
            self._step(MissionStepKind.ANALYZE, f"Q{idx + 1}: no sources to extract from")
            return
        n = self.cfg.extract_chars
        blocks = [
            f"[S{i}] {'FrogRadar signal' if s.kind == MissionSourceKind.KB else 'web page'} | "
            f"{s.title} | {s.url}\n{s.excerpt[:n]}"
            for i, s in enumerate(sources, 1)
        ]
        tag, text = delimit("\n\n".join(blocks), (n + 400) * len(sources))
        m = self.m
        out: Extraction = self._complete(
            "bulk",
            "mission_extract",
            Extraction,
            tag=tag,
            text=text,
            brief=_clip(m.brief, 600),
            question=q["question"],
            max_findings=self.cfg.max_findings_per_question,
            categories=", ".join(c.value for c in SignalCategory),
            jfrog_portfolio=self._portfolio(),
        )
        kept = dropped = 0
        seen: set[str] = set()
        for f in out.findings[: self.cfg.max_findings_per_question]:
            reason = None
            src = sources[f.source - 1] if 1 <= f.source <= len(sources) else None
            key = " ".join(f.statement.lower().split())
            if src is None:
                reason = f"cites an unknown source [S{f.source}]"
            elif key in seen:
                reason = "duplicate of another finding"
            elif not quote_in_text(f.evidence_quote, src.excerpt[:n]):
                reason = "evidence quote not found in the cited source"
            seen.add(key)
            finding = MissionFinding(
                mission_id=self.mission_id,
                sub_question_no=idx + 1,
                statement=f.statement,
                evidence_quote=f.evidence_quote,
                source_id=(src or sources[0]).id,
                confidence=FindingConfidence(f.confidence),
                is_claim=f.is_claim,
                impact=f.impact,
                status=FindingStatus.DROPPED if reason else FindingStatus.NEW,
                dropped_reason=reason,
                meta={"category": f.category.value, "why_jfrog_cares": f.why_jfrog_cares},
            )
            self.s.add(finding)
            kept += reason is None
            dropped += reason is not None
        w.not_found = out.not_found
        self.s.flush()
        summary = f"Q{idx + 1}: {kept} findings extracted"
        if dropped:
            summary += f" · {dropped} dropped (quote or citation check)"
        self._step(
            MissionStepKind.ANALYZE,
            summary,
            output={"kept": kept, "dropped": dropped, "not_found": _clip(out.not_found, 300)},
        )

    def _pending(self, idx: int) -> list[MissionFinding]:
        return list(
            self.s.scalars(
                select(MissionFinding)
                .where(
                    MissionFinding.mission_id == self.mission_id,
                    MissionFinding.sub_question_no == idx + 1,
                    MissionFinding.status == FindingStatus.NEW,
                    MissionFinding.verification == Verification.PENDING,
                )
                .order_by(MissionFinding.id)
            )
        )

    # verify: each finding against its cited source excerpt (different model family)
    def _verify(self, idx: int, q: dict[str, Any]) -> None:
        pending = self._pending(idx)
        if not pending:
            self._step(MissionStepKind.VERIFY, f"Q{idx + 1}: nothing to verify")
            return
        n = self.cfg.extract_chars
        by_source: dict[int, list[MissionFinding]] = {}
        for f in pending:
            by_source.setdefault(f.source_id, []).append(f)
        blocks = []
        for sid, items in by_source.items():
            src = self.s.get(MissionSource, sid)
            assert src is not None
            lines = [f"=== Source: {src.title}\nExcerpt:\n{src.excerpt[:n]}\n"]
            for f in items:
                lines.append(f"Finding F{f.id}: {f.statement}\nQuoted evidence: {f.evidence_quote}")
            blocks.append("\n".join(lines))
        tag, text = delimit("\n\n".join(blocks), (n + 2000) * len(by_source))
        out: Verdicts = self._complete("verify", "mission_verify", Verdicts, tag=tag, text=text)
        verdicts = {v.key.strip().upper().removeprefix("F"): v for v in out.items}
        counts = {"supported": 0, "partial": 0, "dropped": 0}
        for f in pending:
            v = verdicts.get(str(f.id))
            if v is None or v.verdict == "unsupported":
                f.verification = Verification.UNSUPPORTED
                f.status = FindingStatus.DROPPED
                f.dropped_reason = (
                    "verifier: no verdict" if v is None else f"verifier: unsupported – {v.note}"
                )[:500]
                f.verification_notes = v.note[:600] if v else None
                counts["dropped"] += 1
                continue
            f.verification = Verification(v.verdict)
            f.verification_notes = v.note[:600]
            if v.verdict == "partial" and f.confidence == FindingConfidence.HIGH:
                f.confidence = FindingConfidence.MEDIUM  # only supported findings may be high
            counts[v.verdict] += 1
        self._work_for(idx).answered = counts["supported"] + counts["partial"] > 0
        summary = (
            f"Q{idx + 1}: verified {len(pending)} · {counts['supported']} supported"
            f" · {counts['partial']} partly verified · {counts['dropped']} dropped"
        )
        self._step(MissionStepKind.VERIFY, summary, output=counts)

    # report
    def kept_findings(self) -> list[MissionFinding]:
        return list(
            self.s.scalars(
                select(MissionFinding)
                .where(
                    MissionFinding.mission_id == self.mission_id,
                    MissionFinding.status.in_(
                        (FindingStatus.NEW, FindingStatus.APPROVED, FindingStatus.REJECTED)
                    ),
                    MissionFinding.verification.in_((Verification.SUPPORTED, Verification.PARTIAL)),
                )
                .order_by(MissionFinding.sub_question_no, MissionFinding.id)
            )
        )

    def _open_notes(self) -> list[str]:
        out = []
        for i, q in enumerate(self.questions()):
            w = self._work.get(i)
            if w is None:
                out.append(f"Not researched: {q['question']}")
            elif not w.answered:
                out.append(f"Not confirmed: {q['question']}")
            if w is not None and w.not_found:
                out.append(f"Q{i + 1} not found: {w.not_found}")
        return out

    def _report_node(self, state: State) -> dict[str, Any]:
        stop_status = MissionStatus(state["stop_status"]) if state["stop_status"] else None
        reason = state["stop_reason"]
        # Unverified findings never reach the analyst.
        for f in self.s.scalars(
            select(MissionFinding).where(
                MissionFinding.mission_id == self.mission_id,
                MissionFinding.verification == Verification.PENDING,
                MissionFinding.status == FindingStatus.NEW,
            )
        ):
            f.status = FindingStatus.DROPPED
            f.dropped_reason = "not verified (mission stopped before the check)"
        kept = self.kept_findings()
        notes = self._open_notes()
        use_llm = bool(kept) and stop_status in (None, MissionStatus.COMPLETED)
        report: dict[str, Any] = {}
        generated = "fallback"
        if use_llm:
            try:
                if self._spent() >= self.m.budget_usd:
                    raise MissionBudgetExceeded("mission budget reached before the report")
                report = self._llm_report(kept, notes)
                generated = "llm"
            except MissionBudgetExceeded as e:
                self.s.rollback()
                stop_status, reason = MissionStatus.BUDGET_EXCEEDED, f"Budget reached: {e}"
            except Exception as e:  # noqa: BLE001 - fall back to the plain report
                logger.warning("mission %s: report model failed: %s", self.mission_id, e)
                self.s.rollback()
        if generated == "fallback":
            report = {"summary": [], "open_questions": notes[:6]}
        m = self.m
        m.report = {
            **report,
            "generated": generated,
            "stopped_reason": reason,
            "kept": len(kept),
        }
        status = stop_status or MissionStatus.COMPLETED
        m.status = status
        m.finished_at = self.tools.clock()
        if status != MissionStatus.COMPLETED:
            m.error = reason
        n_open = len(report["open_questions"])
        summary = f"Report: {len(kept)} verified findings · {n_open} open questions"
        if reason:
            summary += f" · {reason}"
        self._step(MissionStepKind.REPORT, summary, output={"generated": generated})
        return {}

    def _llm_report(self, kept: list[MissionFinding], notes: list[str]) -> dict[str, Any]:
        lines = [
            f"F{f.id} ({_label(f.verification)}, confidence {f.confidence.value}): {f.statement}"
            for f in kept
        ]
        if notes:
            lines += ["", "Unanswered / not found:", *(f"- {n}" for n in notes)]
        tag, text = delimit("\n".join(lines), 20000)
        out: MissionReport = self._complete(
            "synthesis",
            "mission_report",
            MissionReport,
            tag=tag,
            text=text,
            brief=_clip(self.m.brief, 600),
        )
        ids = {str(f.id) for f in kept}
        points = []
        for p in out.summary:
            cited = [k.strip().upper().removeprefix("F") for k in p.findings]
            cited = [int(c) for c in cited if c in ids]
            if cited:  # a point without a valid finding citation is not shown
                points.append({"text": p.text, "finding_ids": cited})
        return {
            "summary": points,
            "open_questions": out.open_questions,
            "prompt_version": load_prompt("mission_report").id,
        }
