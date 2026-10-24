"""Output models and size caps for the MCP tools (D75).

Every model is built from the read API's DTOs (app.api.schemas), which never carry raw_text, page
excerpts, meta or embeddings. Every item carries the url of its source. Lists are capped at
MAX_ITEMS, text fields at MAX_TEXT chars, and a whole response at MAX_BYTES of JSON (tail items
are dropped and `truncated` is set), so one tool call never floods the client's context.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.api.schemas import PricingItem, SignalOut

MAX_ITEMS = 25
MAX_TEXT = 600
MAX_BYTES = 24_000


def cut(text: str | None, n: int = MAX_TEXT) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class SignalItem(BaseModel):
    id: int
    title: str
    takeaway: str | None
    company: str | None = Field(description="Primary company (or product) the signal is about")
    category: str | None
    impact: int | None = Field(description="1-5 for JFrog (5 = direct threat to the core business)")
    confidence: str | None = Field(description="high / medium / low: fidelity to the source")
    verification: str = Field(description="supported, or partial (= partly verified)")
    vendor_claim: bool
    date: datetime | None
    source: str | None
    url: str = Field(description="The original source; cite it")


class SignalDetail(SignalItem):
    summary: str | None
    why_jfrog_cares: str | None
    evidence_quote: str | None = Field(description="Verbatim quote from the source (verified only)")


class WhatsNew(BaseModel):
    window_days: int = Field(description="The window actually used (may be wider than requested)")
    requested_days: int
    note: str | None = Field(description="Says so when the window was widened or nothing was found")
    newest_signal_at: datetime | None = Field(description="Date of the newest matching signal")
    items: list[SignalItem]
    truncated: bool = False


class SearchHit(BaseModel):
    id: str = Field(description="sig:<id> (use get_signal), bc:<company>:<section>:<n>, radar:<id>")
    type: str = Field(description="article, pricing_page, battlecard or radar")
    title: str
    text: str
    date: datetime | None
    source: str | None
    url: str
    score: float


class SearchResults(BaseModel):
    query: str
    filters: dict[str, Any]
    items: list[SearchHit]
    truncated: bool = False


class Cited(BaseModel):
    text: str
    citations: list[int] = Field(description="Numbers into `sources`")


class SourceRef(BaseModel):
    n: int
    signal_id: int
    title: str
    url: str


class MatrixItem(BaseModel):
    capability: str
    jfrog: str = Field(description="JFrog's level: full / partial / planned / none / unknown")
    jfrog_doc: str | None
    competitor: str = Field(description="Competitor's level from evidence")
    competitor_note: str | None
    evidence_url: str | None
    evidence_title: str | None


class Battlecard(BaseModel):
    company: str
    slug: str
    generated_at: datetime | None
    positioning: Cited | None
    strengths: list[Cited]
    weaknesses: list[Cited]
    opportunities_for_jfrog: list[Cited]
    blind_spots_for_jfrog: list[Cited]
    sources: list[SourceRef]
    matrix: list[MatrixItem] = Field(description="Capabilities with evidence for the competitor")
    capabilities_without_evidence: list[str]
    app_url: str
    note: str | None = Field(None, description="Why there is no card (not enough evidence yet)")
    truncated: bool = False


class PriceChangeItem(BaseModel):
    label: str
    before: str
    after: str


class PricingChange(BaseModel):
    signal_id: int
    company: str | None
    title: str
    date: datetime | None
    changes: list[PriceChangeItem] = Field(
        description="Verified before → after prices (may be empty)"
    )
    summary: str | None = Field(description="What changed, when no exact price pair is verified")
    evidence_quote: str | None
    url: str


class PricingChanges(BaseModel):
    days: int
    items: list[PricingChange]
    truncated: bool = False


class RadarItem(BaseModel):
    id: int
    name: str
    kind: str
    status: str
    score: float | None
    relation: str | None = Field(description="threatens / complements / both / none (to JFrog)")
    relation_products: list[str]
    relation_check: str | None = Field(description="supported, or partial (= partly verified)")
    what_it_is: str | None
    why_notable_now: str | None
    mentions: int
    app_url: str


class RadarList(BaseModel):
    items: list[RadarItem]
    truncated: bool = False


class AskSourceItem(BaseModel):
    n: int
    id: str
    type: str
    title: str
    url: str


class AskAnswer(BaseModel):
    question: str
    not_enough_evidence: bool
    intro: str | None
    points: list[Cited]
    meaning_for_jfrog: Cited | None
    gap: str | None
    suggestions: list[str]
    sources: list[AskSourceItem]
    cost_usd: float | None
    cached: bool


def _company(s: SignalOut) -> str | None:
    primary = next((e for e in s.entities if e.primary), None) or (s.entities or [None])[0]
    return primary.name if primary else None


def signal_item(s: SignalOut) -> SignalItem:
    return SignalItem(
        id=s.id,
        title=cut(s.title, 200) or "",
        takeaway=cut(s.takeaway, 300),
        company=_company(s),
        category=str(s.category) if s.category else None,
        impact=s.impact,
        confidence=s.confidence.band if s.confidence else None,
        verification=str(s.verification),
        vendor_claim=s.is_claim,
        date=s.published_at,
        source=s.source.name if s.source else None,
        url=s.url,
    )


def signal_detail(s: SignalOut) -> SignalDetail:
    quote = s.evidence.quote if s.evidence and s.evidence.verified else None
    return SignalDetail(
        **signal_item(s).model_dump(),
        summary=cut(s.summary),
        why_jfrog_cares=cut(s.why_jfrog_cares),
        evidence_quote=cut(quote, 400),
    )


def pricing_change(p: PricingItem) -> PricingChange:
    s = p.signal
    quote = s.evidence.quote if s.evidence and s.evidence.verified else None
    return PricingChange(
        signal_id=s.id,
        company=_company(s),
        title=cut(s.title, 200) or "",
        date=s.published_at,
        changes=[
            PriceChangeItem(label=cut(c.label, 120) or "", before=c.before, after=c.after)
            for c in p.changes
        ][:10],  # fmt: skip
        summary=None if p.changes else cut(s.summary),
        evidence_quote=cut(quote, 300),
        url=s.url,
    )


def capped[M: BaseModel](model: M, field: str = "items") -> M:
    """Keep at most MAX_ITEMS in `field` and the JSON under MAX_BYTES (drop tail items)."""
    items = list(getattr(model, field))
    truncated = len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]
    model = model.model_copy(update={field: items, "truncated": truncated})
    while items and len(model.model_dump_json()) > MAX_BYTES:
        items = items[:-1]
        model = model.model_copy(update={field: items, "truncated": True})
    return model
