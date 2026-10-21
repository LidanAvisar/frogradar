"""Response models of the read API (/api). The frontend's TypeScript types are generated from these.

Rule (CLAUDE.md, D25): signals.raw_text is never part of a response. No DTO has a raw_text, meta,
embedding or content_hash field; prices extracted from a page diff are short tokens, not lines.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.enums import (
    EntityType,
    Relationship,
    RunStatus,
    SignalCategory,
    SourceKind,
    Verification,
)

Band = Literal["high", "medium", "low"]


class SourceOut(BaseModel):
    name: str
    kind: SourceKind
    trust: float = Field(description="Source trust score, 0-1")


class EvidenceOut(BaseModel):
    quote: str
    verified: bool = Field(
        description="True = found verbatim in the source; unverified quotes are not shown as quotes"
    )
    side: Literal["after", "before"] = Field(
        description="Page diffs: 'before' = the quoted text was removed from the page."
    )
    section: str | None = Field(None, description="Page section path of the quote (page diffs)")


class ConfidenceOut(BaseModel):
    score: float
    band: Band


class EntityRef(BaseModel):
    slug: str
    name: str
    type: EntityType
    primary: bool


class PageDiffOut(BaseModel):
    before_captured_at: datetime | None
    before_origin: Literal["wayback", "live"] | None
    after_captured_at: datetime | None


class SignalOut(BaseModel):
    id: int
    title: str = Field(description="Display title: the generated headline of a page diff, if any")
    original_title: str = Field(description="The title as collected")
    url: str
    kind: Literal["article", "page_diff"]
    source: SourceOut | None
    published_at: datetime | None
    category: SignalCategory | None
    impact: int | None
    is_claim: bool
    summary: str | None
    why_jfrog_cares: str | None
    takeaway: str | None = Field(
        None, description="One sentence: the single most important point for JFrog"
    )
    evidence: EvidenceOut | None
    verification: Verification
    confidence: ConfidenceOut | None
    entities: list[EntityRef]
    sources_count: int = Field(description="Sources reporting this story (dedup cluster size)")
    page_diff: PageDiffOut | None = None


class RankedSignalOut(SignalOut):
    rank: int


class SignalPage(BaseModel):
    items: list[SignalOut]
    total: int
    page: int
    page_size: int


class SinceYesterdayItem(BaseModel):
    id: int
    title: str
    url: str
    impact: int | None
    category: SignalCategory | None
    entities: list[EntityRef]
    published_at: datetime | None


class PriceChange(BaseModel):
    label: str = Field(description="Plan / section the price belongs to")
    before: str
    after: str


class PricingItem(BaseModel):
    signal: SignalOut
    changes: list[PriceChange] = Field(
        description="Before → after prices: model-proposed and verified against the diff in code, "
        "else the deterministic parser; empty = none found"
    )
    changes_source: Literal["verified_llm", "parser"] | None = Field(
        None, description="Where `changes` came from (null = no changes)"
    )


class LastRun(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus


class PipelineInfo(BaseModel):
    last_run: LastRun | None
    active_feeds: int
    watched_pages: int


class BriefWindow(BaseModel):
    days: int
    since: datetime


class CitationRef(BaseModel):
    id: int
    title: str
    url: str
    published_at: datetime | None


class CitedSentenceOut(BaseModel):
    text: str
    citations: list[CitationRef]


class BriefOut(BaseModel):
    generated_at: datetime
    window: BriefWindow
    headline: str | None = Field(None, description="From the latest daily_brief report, if any")
    overview: str | None = Field(None, description="From the latest daily_brief report, if any")
    headline_citations: list[CitationRef] = Field(
        default_factory=list, description="Signals the headline relies on"
    )
    overview_sentences: list[CitedSentenceOut] = Field(
        default_factory=list, description="The overview, sentence by sentence, with its sources"
    )
    top: list[RankedSignalOut]
    since_yesterday: list[SinceYesterdayItem] = Field(
        description="New in the last 24h, without the signals already in top"
    )
    since_yesterday_total: int = Field(description="All new signals, including those in top")
    since_yesterday_high_impact: int = Field(description="New signals with impact >= 4")
    filtered_not_relevant: int
    pricing_watch: list[PricingItem]
    pipeline: PipelineInfo


class EntityOut(BaseModel):
    slug: str
    name: str
    type: EntityType
    relationship: Relationship
    is_self: bool
    signal_count: int
    last_signal_at: datetime | None


class SelfOut(BaseModel):
    """Our own company (JFrog) and the terms the UI highlights as its products."""

    slug: str
    name: str
    product_terms: list[str] = Field(
        description="Full product names and aliases, longest first: always ours"
    )
    bare_terms: list[str] = Field(
        description=(
            "Product names without the company prefix ('Advanced Security'), longest first: ours "
            "unless another capitalized word comes right before ('GitHub Advanced Security')"
        )
    )


class NavCounts(BaseModel):
    signals: int
    review_queue: int


# ---------- battlecards ----------


class CitedText(BaseModel):
    text: str
    signal_ids: list[int]


class BattlecardEntity(BaseModel):
    slug: str
    name: str
    relationship: Relationship


class CapabilityRef(BaseModel):
    slug: str
    name: str
    segment: str


Level = Literal["full", "partial", "planned", "none", "unknown"]


class JFrogLevel(BaseModel):
    level: Level
    note: str
    source: str | None = Field(None, description="Public JFrog doc the level is based on")


class CompetitorLevel(BaseModel):
    level: Level
    note: str | None
    evidence: CitationRef | None
    last_verified_at: datetime | None


class MatrixRow(BaseModel):
    capability: CapabilityRef
    jfrog: JFrogLevel
    competitor: CompetitorLevel
    changed_recently: bool = Field(description="The competitor's level changed in the last days")


class TimelineItem(BaseModel):
    id: int
    date: datetime | None
    title: str
    category: SignalCategory | None
    impact: int | None
    url: str


class Coverage(BaseModel):
    signals: int
    high_impact: int
    sources: int
    capabilities_known: int
    capabilities_total: int
    first_signal_at: datetime | None
    last_signal_at: datetime | None


class BattlecardOut(BaseModel):
    entity: BattlecardEntity
    report_id: int | None
    generated_at: datetime | None
    model: str | None
    positioning: CitedText | None
    strengths: list[CitedText]
    weaknesses: list[CitedText]
    opportunities_for_jfrog: list[CitedText]
    blind_spots_for_jfrog: list[CitedText]
    citations: list[CitationRef] = Field(description="Every signal the card's items cite")
    matrix: list[MatrixRow]
    timeline: list[TimelineItem]
    coverage: Coverage
