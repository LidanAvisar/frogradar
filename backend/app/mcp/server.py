"""FrogRadar MCP server (S16, D75): FrogRadar's intelligence for Claude, Cursor and other MCP clients.

Thin lenses on the existing read layer (app.api.*, app.pipeline.scope, app.rag) – no HTTP calls to
our own API, no new intelligence logic. Tools return capped, source-linked models (app.mcp.shape);
raw_text and page excerpts never leave the server.

    tools:     whats_new, search_intel, get_signal, get_battlecard, pricing_changes, radar, ask
    resources: frogradar://brief/today, frogradar://battlecards
    prompts:   morning_brief, competitor_briefing(company)

Only `ask` calls a language model (paid, budget-guarded, cached). `search_intel` embeds the query
(one cached embedding, a fraction of a cent) and calls no language model.
"""

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SDKToolError
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.llm import BudgetExceeded, DemoCacheMiss, LLMError
from app.db.enums import AskSource, EntityType, Verification
from app.db.models import Entity, RadarCandidate, Signal, signal_entities
from app.mcp import shape
from app.mcp.shape import (
    AskAnswer,
    AskSourceItem,
    Battlecard,
    Cited,
    MatrixItem,
    PricingChanges,
    RadarItem,
    RadarList,
    SearchHit,
    SearchResults,
    SignalDetail,
    SourceRef,
    WhatsNew,
    capped,
    cut,
)
from app.rag.service import AskInputError, LLMFns

logger = logging.getLogger(__name__)

WIDEN = (1, 7, 30)
INSTRUCTIONS = """FrogRadar is JFrog's competitive-intelligence system. Every item it returns comes from a
collected, model-checked source: cite the `url` of each item you use, mark vendor claims and partly verified
items as such, and say plainly when FrogRadar has no evidence instead of filling the gap from general
knowledge. Prefer the free tools (whats_new, search_intel, get_signal, get_battlecard, pricing_changes,
radar); use `ask` only when a synthesized, cited answer is needed (it costs money)."""

SessionFactory = Callable[[], AbstractContextManager[Session]]


class ToolError(SDKToolError):
    """A clear message for the model (unknown company, id, budget reached, ...): the SDK passes
    the text of this exception to the client; any other exception is masked as a crash."""


def _default_sessions() -> SessionFactory:
    from app.db.session import get_engine

    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)

    @contextmanager
    def open_session() -> Iterator[Session]:
        with factory() as s:
            yield s

    return open_session


def _default_llm() -> LLMFns:
    from app.core.llm import complete, embed

    return LLMFns(complete=complete, embed=embed)


def _companies(session: Session) -> list[Entity]:
    return list(
        session.scalars(
            select(Entity)
            .where(Entity.type == EntityType.COMPANY, Entity.parent_id.is_(None))
            .order_by(Entity.name)
        )
    )


def resolve_company(session: Session, value: str) -> Entity:
    """A company by slug or name (case-insensitive); a product resolves to its company."""
    v = value.strip().lower()
    ent = session.scalar(
        select(Entity).where(or_(func.lower(Entity.slug) == v, func.lower(Entity.name) == v))
    )
    while ent is not None and ent.parent_id is not None:
        ent = session.get(Entity, ent.parent_id)
    if ent is None:
        valid = ", ".join(e.slug for e in _companies(session))
        raise ToolError(f"Unknown company {value!r}. Tracked companies (slugs): {valid}")
    return ent


def _family(session: Session, ent: Entity) -> list[int]:
    return [ent.id, *session.scalars(select(Entity.id).where(Entity.parent_id == ent.id))]


def build_server(
    sessions: SessionFactory | None = None,
    llm: Callable[[], LLMFns] | None = None,
    now: Callable[[], datetime] | None = None,
) -> MCPServer:
    """The FrogRadar MCP server. Tests inject a session factory, fake LLM and a pinned clock."""
    open_session = sessions or _default_sessions()
    get_llm = llm or _default_llm
    clock = now or (lambda: datetime.now(UTC))
    app_url = get_settings().app_url.rstrip("/")
    mcp = MCPServer(name="FrogRadar", instructions=INSTRUCTIONS, version="0.1.0")

    # ---------- signals ----------

    def _ranked(s: Session, since: datetime, until: datetime, min_impact: int,
                ids: list[int] | None) -> list[Signal]:  # fmt: skip
        from app.pipeline.scope import ranked

        q = ranked(since, until).where(Signal.impact >= min_impact)
        if ids is not None:
            q = q.where(Signal.id.in_(select(signal_entities.c.signal_id).where(
                signal_entities.c.entity_id.in_(ids))))  # fmt: skip
        return list(s.scalars(q.limit(shape.MAX_ITEMS + 1)))

    @mcp.tool(
        description=(
            "What's new in competitive intelligence: the most important recent signals (launches, "
            "pricing changes, partnerships, incidents...), ranked by impact for JFrog × confidence. "
            "Use it for 'what happened today / this week', optionally for one company. If nothing "
            "matches in the requested window it widens to 7, then 30 days and says so in `note`. "
            "Free (no language model). Cite each item's url."
        )
    )
    def whats_new(
        days: Annotated[int, Field(ge=1, le=90, description="Window in days, ending now")] = 1,
        min_impact: Annotated[int, Field(ge=1, le=5, description="Minimum impact 1-5")] = 3,
        company: Annotated[
            str | None, Field(description="Company slug or name, e.g. sonatype")
        ] = None,
    ) -> WhatsNew:
        from app.api.queries import to_outs
        from app.pipeline.scope import event_time, usable

        with open_session() as s:
            ids = _family(s, resolve_company(s, company)) if company else None
            t = clock()
            windows = [days, *[w for w in WIDEN if w > days]]
            rows: list[Signal] = []
            used = days
            for w in windows:
                used = w
                rows = _ranked(s, t - timedelta(days=w), t, min_impact, ids)
                if rows:
                    break
            newest_q = select(func.max(event_time())).where(usable(), Signal.impact >= min_impact)
            if ids is not None:
                newest_q = newest_q.where(Signal.id.in_(select(signal_entities.c.signal_id).where(
                    signal_entities.c.entity_id.in_(ids))))  # fmt: skip
            newest = s.scalar(newest_q)
            label = "day" if days == 1 else f"{days} days"
            note = None
            if rows and used != days:
                note = f"No new signals in the last {label}; showing the last {used} days."
            elif not rows:
                note = f"No signals with impact ≥ {min_impact} in the last {used} days" + (
                    f"; the newest is from {newest.date().isoformat()}." if newest else "."
                )
            out = WhatsNew(
                window_days=used,
                requested_days=days,
                note=note,
                newest_signal_at=newest,
                items=[shape.signal_item(o) for o in to_outs(s, rows)],
            )
            return capped(out)

    @mcp.tool(
        description=(
            "Search FrogRadar's intelligence (signals, battlecard items, Competitor Radar profiles) "
            "with hybrid retrieval: semantic + keyword. Returns ranked hits with ids and source urls, "
            "no synthesized answer. Use it to find evidence for a question; then get_signal(id) for "
            "details. Free of language-model calls (one cached query embedding)."
        )
    )
    def search_intel(
        query: Annotated[str, Field(min_length=2, max_length=300)],
        company: Annotated[str | None, Field(description="Company slug or name")] = None,
        days: Annotated[int | None, Field(ge=1, le=365, description="Only the last N days")] = None,
    ) -> SearchResults:
        from dataclasses import replace

        from app.core.catalog import load_pipeline
        from app.rag.filters import infer_filters
        from app.rag.retrieve import retrieve

        with open_session() as s:
            t = clock()
            f = infer_filters(s, query, t)
            if company:
                ent = resolve_company(s, company)
                f = replace(f, company_slug=ent.slug, company_name=ent.name,
                            entity_ids=tuple(_family(s, ent)))  # fmt: skip
            if days:
                f = replace(f, since=t - timedelta(days=days), until=t, window=f"last {days} days")
            cfg = load_pipeline().ask
            cfg = cfg.model_copy(
                update={"k": min(12, cfg.max_docs), "max_docs": max(12, cfg.max_docs)}
            )
            try:
                found = retrieve(s, query, f, embed=get_llm().embed, cfg=cfg)
            except BudgetExceeded as e:
                raise ToolError(
                    f"FrogRadar's LLM budget is reached; try again tomorrow ({e})."
                ) from None
            except LLMError as e:
                raise ToolError(
                    "search_intel could not embed the query (is the embedding API key set in the "
                    f"server's environment?): {type(e).__name__}. Use whats_new or get_battlecard instead."
                ) from None
            hits = [
                SearchHit(
                    id=h.doc.id,
                    type=h.doc.type,
                    title=cut(h.doc.title, 200) or "",
                    text=cut(h.doc.text, 500) or "",
                    date=h.doc.date,
                    source=h.doc.source,
                    url=h.doc.url if h.doc.url.startswith("http") else app_url + h.doc.url,
                    score=round(h.score, 5),
                )
                for h in found.hits
            ]
            filters = {
                k: v for k, v in {"company": f.company_slug, "window": f.window}.items() if v
            }
            return capped(SearchResults(query=query, filters=filters, items=hits))

    @mcp.tool(
        description=(
            "One signal in full: summary, why JFrog cares, the verified evidence quote, impact, "
            "confidence and the source url. Ids come from whats_new / search_intel (sig:<id>)."
        )
    )
    def get_signal(
        id: Annotated[int, Field(description="Signal id, e.g. 1055 (from sig:1055)")],  # noqa: A002
    ) -> SignalDetail:
        from app.api.queries import to_outs
        from app.pipeline.scope import visible

        with open_session() as s:
            sig = s.scalar(select(Signal).where(Signal.id == id, visible()))
            if sig is None:
                raise ToolError(f"No signal {id} (unknown, rejected or not enriched yet).")
            (out,) = to_outs(s, [sig])
            return shape.signal_detail(out)

    # ---------- battlecards, pricing, radar ----------

    def _battlecard(s: Session, company: str) -> Battlecard:
        from app.api.battlecards import get_battlecard as api_card

        ent = resolve_company(s, company)
        try:
            card = api_card(ent.slug, s, clock())
        except HTTPException as e:
            raise ToolError(str(e.detail)) from None
        numbers = {c.id: i for i, c in enumerate(card.citations, 1)}

        def cited(items: list[Any]) -> list[Cited]:
            return [Cited(text=cut(it.text) or "", citations=[numbers[i] for i in it.signal_ids
                                                               if i in numbers]) for it in items]  # fmt: skip

        known = [r for r in card.matrix if r.competitor.level != "unknown"]
        out = Battlecard(
            company=card.entity.name,
            slug=card.entity.slug,
            generated_at=card.generated_at,
            positioning=cited([card.positioning])[0] if card.positioning else None,
            strengths=cited(card.strengths),
            weaknesses=cited(card.weaknesses),
            opportunities_for_jfrog=cited(card.opportunities_for_jfrog),
            blind_spots_for_jfrog=cited(card.blind_spots_for_jfrog),
            sources=[
                SourceRef(n=numbers[c.id], signal_id=c.id, title=cut(c.title, 200) or "", url=c.url)
                for c in card.citations
            ],  # fmt: skip
            matrix=[
                MatrixItem(
                    capability=r.capability.name,
                    jfrog=str(r.jfrog.level),
                    jfrog_doc=r.jfrog.source,
                    competitor=str(r.competitor.level),
                    competitor_note=cut(r.competitor.note, 200),
                    evidence_url=r.competitor.evidence.url if r.competitor.evidence else None,
                    evidence_title=cut(r.competitor.evidence.title, 160)
                    if r.competitor.evidence
                    else None,
                )
                for r in known
            ],
            capabilities_without_evidence=[
                r.capability.name for r in card.matrix if r.competitor.level == "unknown"
            ],  # fmt: skip
            app_url=f"{app_url}/battlecards/{card.entity.slug}",
        )
        return capped(out, "sources")

    @mcp.tool(
        description=(
            "A competitor's battlecard: positioning, strengths, weaknesses, opportunities and blind "
            "spots for JFrog (each with numbered citations into `sources`), and the capability "
            "matrix (JFrog's level vs the competitor's, with an evidence link per cell). Use it "
            "before a sales call or to compare products. Free."
        )
    )
    def get_battlecard(
        company: Annotated[str, Field(description="Company slug or name, e.g. sonatype, github")],
    ) -> Battlecard:
        with open_session() as s:
            return _battlecard(s, company)

    @mcp.tool(
        description=(
            "Pricing-page changes FrogRadar detected (it diffs competitors' pricing pages): verified "
            "before → after prices where they could be paired exactly, otherwise the summary and a "
            "verified quote. Free."
        )
    )
    def pricing_changes(
        company: Annotated[str | None, Field(description="Company slug or name")] = None,
        days: Annotated[int, Field(ge=1, le=365)] = 90,
    ) -> PricingChanges:
        from app.api.brief import _pricing_item
        from app.api.queries import is_page_diff, signals_query, to_outs
        from app.pipeline.scope import event_time, visible

        with open_session() as s:
            t = clock()
            q = signals_query().where(
                visible(), is_page_diff(), Signal.relevant.is_(True),
                Signal.verification != Verification.UNSUPPORTED,
                event_time() >= t - timedelta(days=days), event_time() <= t,
            )  # fmt: skip
            if company:
                ids = _family(s, resolve_company(s, company))
                q = q.where(Signal.id.in_(select(signal_entities.c.signal_id).where(
                    signal_entities.c.entity_id.in_(ids))))  # fmt: skip
            rows = list(s.scalars(q.order_by(event_time().desc(), Signal.id.desc()).limit(40)))
            items = [shape.pricing_change(_pricing_item(sig, out))
                     for sig, out in zip(rows, to_outs(s, rows), strict=True)]  # fmt: skip
            return capped(PricingChanges(days=days, items=items))

    @mcp.tool(
        description=(
            "Competitor Radar: new players nobody configured yet (startups, open-source projects), "
            "ranked by an emergence score, with what they are, how they relate to JFrog "
            "(threatens / complements / both) and why they are notable now. Use it to check whether "
            "a new tool is a threat. Free."
        )
    )
    def radar(
        limit: Annotated[int, Field(ge=1, le=25)] = 10,
        relation: Annotated[
            Literal["threatens", "complements", "both"] | None,
            Field(description="Only candidates with this relation to JFrog"),
        ] = None,
    ) -> RadarList:
        from app.api.radar import OPEN, _live, _product_names, to_out, top_section

        with open_session() as s:
            q = select(RadarCandidate).where(
                _live(), RadarCandidate.status.in_(OPEN), top_section()
            )
            if relation:
                q = q.where(RadarCandidate.metrics["relation"].astext == relation)
            rows = s.scalars(q.order_by(RadarCandidate.score.desc().nulls_last(),
                                        RadarCandidate.id).limit(limit))  # fmt: skip
            names = _product_names(s)
            items = []
            for c in rows:
                o = to_out(c, names)
                items.append(RadarItem(
                    id=o.id, name=o.name, kind=str(o.kind), status=str(o.status),
                    score=round(o.score, 1) if o.score is not None else None,
                    relation=o.relation, relation_products=o.relation_products,
                    relation_check=o.relation_check, what_it_is=cut(o.what_it_is, 300),
                    why_notable_now=cut(o.why_notable_now, 300), mentions=o.mentions_count,
                    app_url=f"{app_url}/radar/{o.id}",
                ))  # fmt: skip
            return capped(RadarList(items=items))

    # ---------- ask (paid) ----------

    @mcp.tool(
        description=(
            "Ask FrogRadar a question in plain language and get a checked answer: 2-6 points, each "
            "citing FrogRadar documents, verified by a second model; or 'not enough evidence' with "
            "what is missing. PAID: it calls language models (about $0.02 per new question), goes "
            "through FrogRadar's daily budget guard and its LLM cache (a repeated question over the "
            "same documents is free). Prefer search_intel / get_battlecard for lookups."
        )
    )
    def ask(question: Annotated[str, Field(min_length=3, max_length=500)]) -> AskAnswer:
        from app.rag.service import ask as rag_ask

        with open_session() as s:
            try:
                out = rag_ask(s, question, conversation_id=None, llm=get_llm(), now=clock(),
                              source=AskSource.MCP)  # fmt: skip
            except AskInputError as e:
                raise ToolError(str(e)) from None
            except BudgetExceeded as e:
                raise ToolError(
                    f"FrogRadar's LLM budget is reached; try again tomorrow ({e})."
                ) from None
            except DemoCacheMiss:
                raise ToolError("Demo mode answers only questions it has seen before.") from None
            except LLMError as e:
                raise ToolError(f"The language model failed ({type(e).__name__}).") from None
            return AskAnswer(
                question=out.standalone_question,
                not_enough_evidence=out.not_enough_evidence,
                intro=out.intro,
                points=[
                    Cited(text=cut(f"{p.lead}: {p.text}") or "", citations=p.citations)
                    for p in out.points
                ],  # fmt: skip
                meaning_for_jfrog=Cited(
                    text=cut(out.meaning_for_jfrog.text) or "",
                    citations=out.meaning_for_jfrog.citations,
                )
                if out.meaning_for_jfrog
                else None,  # fmt: skip
                gap=out.gap,
                suggestions=out.suggestions[:5],
                sources=[
                    AskSourceItem(
                        n=x.n,
                        id=x.id,
                        type=str(x.type),
                        title=cut(x.title, 200) or "",
                        url=x.url if x.url.startswith("http") else app_url + x.url,
                    )
                    for x in out.sources
                ],  # fmt: skip
                cost_usd=float(out.cost_usd) if out.cost_usd is not None else None,
                cached=out.cached,
            )

    # ---------- resources ----------

    @mcp.resource(
        "frogradar://brief/today",
        name="Morning Brief",
        description="Today's Morning Brief: top story (cited), top developments of the last 7 days, "
        "pricing watch. JSON.",
        mime_type="application/json",
    )
    def brief_today() -> str:
        from app.api.brief import get_brief

        with open_session() as s:
            b = get_brief(s, clock(), days=7, limit=5)
            story = None
            if b.top_story:
                ts = b.top_story
                story = {
                    "title": cut(ts.title, 200),
                    "company": ts.company,
                    "what_happened": cut(ts.what_happened.text) if ts.what_happened else None,
                    "why_it_matters": cut(ts.why_it_matters.text) if ts.why_it_matters else None,
                    "suggested_move": cut(ts.suggested_move.text) if ts.suggested_move else None,
                    "sources": [
                        {"title": cut(c.title, 160), "url": c.url}
                        for c in (ts.title_citations or [])
                    ][:5],  # fmt: skip
                    "signal": shape.signal_item(ts.signal).model_dump(mode="json")
                    if ts.signal
                    else None,
                }
            data = {
                "date": clock().date().isoformat(),
                "stats": b.stats.model_dump(mode="json") if b.stats else None,
                "top_story": story,
                "developments": [
                    shape.signal_item(d).model_dump(mode="json") for d in b.developments
                ],
                "pricing_watch": [
                    shape.pricing_change(p).model_dump(mode="json") for p in b.pricing_watch
                ][:5],  # fmt: skip
                "app_url": app_url,
            }
            return _json_capped(data)

    @mcp.resource(
        "frogradar://battlecards",
        name="Battlecards",
        description="Tracked competitors with a battlecard (slug for get_battlecard), JSON.",
        mime_type="application/json",
    )
    def battlecards() -> str:
        from app.api.battlecards import list_battlecards

        with open_session() as s:
            items = list_battlecards(s)
            return _json_capped({"items": [
                {"slug": i.slug, "name": i.name, "relationship": str(i.relationship),
                 "signals": i.signal_count, "generated_at": i.generated_at.isoformat()
                 if i.generated_at else None, "app_url": f"{app_url}/battlecards/{i.slug}"}
                for i in items]})  # fmt: skip

    # ---------- prompts ----------

    @mcp.prompt(description="Today's competitive-intelligence brief for JFrog, from FrogRadar.")
    def morning_brief() -> str:
        return (
            "Give me today's competitive-intelligence brief for JFrog from FrogRadar.\n"
            "1. Read the resource frogradar://brief/today (or call whats_new with days=1).\n"
            "2. Lead with the top story: what happened, why it matters to JFrog, the suggested move.\n"
            "3. Then up to 5 other developments, one line each with impact and company.\n"
            "4. Add pricing changes if any (pricing_changes with days=7).\n"
            "Cite the source url of every item. Mark vendor claims and partly verified items. If "
            "FrogRadar has nothing new, say so and give the date of its newest signal."
        )

    @mcp.prompt(description="A competitor briefing (e.g. before a sales call), from FrogRadar.")
    def competitor_briefing(
        company: Annotated[str, Field(description="Company slug or name, e.g. sonatype")],
    ) -> str:
        return (
            f"Brief me on {company} as a JFrog competitor, using FrogRadar only.\n"
            f"1. get_battlecard('{company}'): positioning, strengths, weaknesses, where JFrog wins "
            "(opportunities) and where it is exposed (blind spots), and the capability matrix.\n"
            f"2. whats_new(days=30, company='{company}') for recent moves and "
            f"pricing_changes(company='{company}') for price changes.\n"
            "3. Close with 3 talking points for a sales call, each backed by a cited item.\n"
            "Cite source urls. Say plainly where FrogRadar has no evidence (capabilities without "
            "evidence stay unknown; do not fill them from general knowledge)."
        )

    return mcp


def _json_capped(data: dict[str, Any]) -> str:
    text = json.dumps(data, default=str)
    while len(text) > shape.MAX_BYTES:
        lists = [k for k, v in data.items() if isinstance(v, list) and v]
        if not lists:
            break
        biggest = max(lists, key=lambda k: len(json.dumps(data[k], default=str)))
        data[biggest] = data[biggest][:-1]
        data["truncated"] = True
        text = json.dumps(data, default=str)
    return text
