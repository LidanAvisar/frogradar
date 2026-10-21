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
    title: str
    url: str
    kind: Literal["article", "page_diff"]
    source: SourceOut | None
    published_at: datetime | None
    category: SignalCategory | None
    impact: int | None
    is_claim: bool
    summary: str | None
    why_jfrog_cares: str | None
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
        description="Deterministic before → after price pairs from the diff; empty = none found"
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


class BriefOut(BaseModel):
    generated_at: datetime
    window: BriefWindow
    headline: str | None = Field(None, description="From the latest daily_brief report, if any")
    overview: str | None = Field(None, description="From the latest daily_brief report, if any")
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
