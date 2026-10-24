"""Which signals later pipeline stages (dedup, enrichment, verification) work on.

A signal is in scope if its source is active, or if it has no source (research-mission findings).
Signals from inactive sources stay in the DB (history, citations) but are not processed further.
Every stage selects its work through these helpers, so "in scope" has one definition.

Two other selections exist for evaluation only (app.eval), never for the daily pipeline:
- full_catalog(): every signal, any source (dedup calibration, eval-set sampling).
- eval_set(ids): exactly the labeled signals of the eval set (eval/labels.csv).
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import ColumnElement, Select, and_, exists, func, or_, select, true
from sqlalchemy.orm import Session

from app.db.enums import ReviewStatus, Verification
from app.db.models import Signal, Source

# Verdicts whose signals may be shown and synthesized (unsupported ones never are).
SHOWN_VERIFICATIONS = (Verification.SUPPORTED, Verification.PARTIAL)


def in_scope() -> ColumnElement[bool]:
    """WHERE clause for signals the pipeline should process."""
    # correlate_except: stays a real subquery even when the caller's query joins `sources`.
    active_source = (
        exists().where(Source.id == Signal.source_id, Source.is_active).correlate_except(Source)
    )
    return or_(Signal.source_id.is_(None), active_source)


def not_stripped() -> ColumnElement[bool]:
    """Signals loaded from the seed data (make seed) have no raw_text (D77, D83): the pipeline never
    re-processes them (embed, link, triage, enrich, verify, radar); their enrichment stays."""
    return Signal.meta["snapshot_stripped"].astext.is_distinct_from("true")


def in_scope_signals() -> Select[tuple[Signal]]:
    """`select(Signal)` limited to in-scope signals; stages add their own filters/order."""
    return select(Signal).where(in_scope())


def count_in_scope(session: Session) -> tuple[int, int]:
    """(in scope, total) signal counts."""
    total = session.scalar(select(func.count()).select_from(Signal)) or 0
    scoped = session.scalar(select(func.count()).select_from(Signal).where(in_scope())) or 0
    return scoped, total


def full_catalog() -> ColumnElement[bool]:
    """WHERE clause for every signal, active source or not (evaluation and calibration only)."""
    return true()


def eval_set(ids: Iterable[int]) -> ColumnElement[bool]:
    """WHERE clause for the labeled eval-set signals, in scope or not (evaluation only)."""
    return Signal.id.in_(sorted(set(ids)))


# ---------- discovery signals (Competitor Radar, D57) ----------


def discovery() -> ColumnElement[bool]:
    """Signals from the discovery sources (Hacker News, GitHub search, CNCF landscape):
    meta.kind = "discovery". They are triaged and enriched like any signal, but feed only the
    Competitor Radar: they are not verified one by one, not deduplicated against the news, and
    never shown in the Brief, battlecards or the signal feed."""
    return Signal.meta["kind"].astext == "discovery"


def not_discovery() -> ColumnElement[bool]:
    return Signal.meta["kind"].astext.is_distinct_from("discovery")


# ---------- research-mission findings (D73, D82) ----------


def not_mission() -> ColumnElement[bool]:
    """Not an approved research-mission finding (meta.kind = "mission"). Findings are research,
    not news: they have no publish date, so by fetch time they would look like today's news.
    They stay in battlecards, Ask / RAG, the signal feed and the mission screen, but not in the
    Brief's ranking, "Since yesterday", the daily brief's input or the MCP whats_new tool (D82)."""
    return Signal.meta["kind"].astext.is_distinct_from("mission")


# ---------- what screens and synthesis may use ----------


def canonical() -> ColumnElement[bool]:
    """The canonical member of its dedup cluster (or not clustered)."""
    return or_(Signal.dedup_cluster_id.is_(None), Signal.dedup_cluster_id == Signal.id)


def reviewable() -> ColumnElement[bool]:
    """In scope, canonical, enriched, not discovery: what screens may show and analysts review,
    rejected signals included (the review actions need them for a 409)."""
    return and_(in_scope(), canonical(), Signal.enriched_at.is_not(None), not_discovery())


def visible() -> ColumnElement[bool]:
    """In scope, canonical and enriched: what any screen may show (D47). Rows whose text changed
    after enrichment have enriched_at reset and stay hidden until re-enriched. Discovery signals
    are never visible here: the Radar shows them as candidate evidence instead (D57). Signals an
    analyst rejected stay in the DB but leave every screen, synthesis and RAG (D68)."""
    return and_(reviewable(), Signal.status != ReviewStatus.REJECTED)


def usable() -> ColumnElement[bool]:
    """Visible, relevant and not unsupported: what synthesized outputs may cite."""
    return and_(visible(), Signal.relevant.is_(True), Signal.verification.in_(SHOWN_VERIFICATIONS))


def event_time() -> ColumnElement[datetime]:
    """When the event happened: publish date, else when we first saw it."""
    return func.coalesce(Signal.published_at, Signal.fetched_at)


def ranked(since: datetime, until: datetime) -> Select[tuple[Signal]]:
    """Usable, scored news signals of a window, best first: impact × confidence, then newest.
    One ranking for the Brief's Top 5 (API) and the daily brief's input (pipeline). Research-mission
    findings are not news and never rank here (D82)."""
    return (
        select(Signal)
        .where(
            usable(),
            not_mission(),
            Signal.impact.is_not(None),
            Signal.confidence.is_not(None),
            event_time() >= since,
            event_time() <= until,
        )
        .order_by((Signal.impact * Signal.confidence).desc(), event_time().desc(), Signal.id.desc())
    )
