"""Response models of the read API (/api). The frontend's TypeScript types are generated from these.

raw_text rule (D25): signals.raw_text is never part of a response. No DTO has a raw_text, meta,
embedding or content_hash field; prices extracted from a page diff are short tokens, not lines.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.db.enums import (
    EntityType,
    ReasonCode,
    Relationship,
    ReviewStatus,
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


class SignalReviewOut(BaseModel):
    status: ReviewStatus
    by: str | None = Field(None, description="Analyst name of the last action")
    at: datetime | None = None
    edited_fields: list[str] = Field(
        default_factory=list,
        description="Fields an analyst corrected (category, impact, relevant, primary_entity)",
    )


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
    review: SignalReviewOut | None = Field(
        None, description="Analyst review (null = not reviewed yet)"
    )


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


class StoryRowOut(BaseModel):
    text: str
    citations: list[CitationRef]


class TopStoryOut(BaseModel):
    """The one story the Brief leads with: from today's daily_brief report, or – when there is
    none (or its rows no longer check out) – the top-ranked signal."""

    source: Literal["daily_brief", "top_signal"]
    title: str
    title_citations: list[CitationRef]
    what_happened: StoryRowOut | None
    why_it_matters: StoryRowOut | None
    suggested_move: StoryRowOut | None = Field(
        None, description="Only from a daily brief; the fallback has none"
    )
    signal: SignalOut = Field(description="Lead signal: confidence, source link")
    company: str | None = Field(None, description="Company the story is about, if one")
    affects: list[str] = Field(
        default_factory=list, description="JFrog products the story names (from config)"
    )
    signal_ids: list[int] = Field(description="Every signal the story cites")


class BriefStats(BaseModel):
    new_signals: int = Field(description="Relevant signals published in the last 24h")
    high_impact: int = Field(description="Of those, impact >= 4")
    price_changes: int = Field(description="Pricing-page changes in the last price_change_days")
    price_change_days: int


class BriefOut(BaseModel):
    generated_at: datetime
    window: BriefWindow
    stats: BriefStats
    top_story: TopStoryOut | None
    developments: list[RankedSignalOut] = Field(
        description="Other developments: the next ranked signals, without the top story's"
    )
    since_yesterday: list[SinceYesterdayItem] = Field(
        description="New in the last 24h, without the signals already shown above"
    )
    filtered_not_relevant: int
    pricing_watch: list[PricingItem]
    pipeline: PipelineInfo
    radar: list["RadarTeaser"] = Field(
        default_factory=list,
        description="New on the radar: top candidates (new / watching) by emergence score",
    )


class EntityOut(BaseModel):
    id: int
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
    radar_new: int = Field(
        0,
        description="New radar candidates in the page's top section (score >= the lower-relevance "
        "threshold, relation not none)",
    )


# ---------- battlecards ----------


class CitedText(BaseModel):
    text: str
    signal_ids: list[int]


class BattlecardEntity(BaseModel):
    slug: str
    name: str
    relationship: Relationship
    segments: list[str] = Field(default_factory=list)


class BattlecardListItem(BaseModel):
    slug: str
    name: str
    relationship: Relationship
    signal_count: int
    generated_at: datetime | None = Field(description="Latest card; null = no card yet")


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


# ---------- competitor radar ----------

RadarKindOut = Literal["company", "product", "open_source_project"]
RadarStatusOut = Literal["new", "watching", "approved", "dismissed"]
RadarRelation = Literal["threatens", "complements", "both", "none"]
DismissReason = Literal[
    "not_relevant", "not_a_competitor", "too_small", "duplicate", "already_tracked", "other"
]


class RadarScoreComponent(BaseModel):
    value: dict[str, Any] = Field(description="Raw inputs of this component")
    factor: float = Field(description="0-1")
    weight: float
    points: float = Field(description="100 × weight × factor")


class RadarBreakdown(BaseModel):
    formula: str
    score: float
    components: dict[str, RadarScoreComponent]


class RadarMetrics(BaseModel):
    mentions: int = 0
    sources: int = 0
    mentions_recent: int = 0
    stars: int | None = None
    star_velocity: float | None = Field(None, description="Stars per day")
    star_velocity_basis: Literal["snapshots", "age"] | None = Field(
        None, description="snapshots = between runs; age = stars / repo age (first sighting)"
    )
    hn_points_max: int | None = None
    max_impact: int | None = Field(None, description="Highest enrich impact of the mentions")
    relation: Literal["threatens", "complements", "both", "none"] | None = Field(
        None, description="JFrog relation from the profile, used for ranking"
    )


class RadarCandidateOut(BaseModel):
    id: int
    name: str
    kind: RadarKindOut
    status: RadarStatusOut
    homepage: str | None
    repo_url: str | None
    score: float | None
    breakdown: RadarBreakdown | None
    sources_count: int
    mentions_count: int
    first_seen: datetime
    last_seen: datetime
    segments: list[str]
    line: str | None = Field(
        None, description="One line: the profile's why-notable-now, else what it is, else a context"
    )
    line_source: Literal["why_notable_now", "what_it_is", "context"] | None = None
    what_it_is: str | None = Field(None, description="Profile statement (accepted profiles only)")
    what_it_is_check: Literal["supported", "partial"] | None = Field(
        None, description='partial = shown as "Partly verified"'
    )
    why_notable_now: str | None = Field(
        None, description="Profile statement (accepted profiles only)"
    )
    why_notable_now_check: Literal["supported", "partial"] | None = None
    relation: RadarRelation | None = Field(
        None, description="JFrog relation used for ranking (null = no trusted profile relation)"
    )
    relation_products: list[str] = Field(
        default_factory=list, description="JFrog product names the relation statement names"
    )
    relation_check: Literal["supported", "partial"] | None = Field(
        None,
        description="Verifier verdict of the relation statement: partial = core right, a detail "
        'overstated (shown as "Partly verified" with the verifier\'s note)',
    )
    profile_status: Literal["none", "ok", "rejected"]
    origins: list[str] = Field(
        description="How mentions reached the radar: news_new_entity, hn_new_entity, "
        "github_search, cncf_landscape, ..."
    )
    metrics: RadarMetrics


class RadarListOut(BaseModel):
    items: list[RadarCandidateOut]
    total: int
    lower_relevance_below: float = Field(
        description="Score below which (or with relation none) the UI lists a candidate under "
        "'Lower relevance' (config/discovery.yaml radar.ui)"
    )


class RadarStatementOut(BaseModel):
    key: Literal["what_it_is", "segments", "jfrog_relation", "stage", "why_notable_now"]
    text: str
    citations: list[CitationRef]
    segments: list[str] | None = None
    relation: Literal["threatens", "complements", "both", "none"] | None = None
    products: list[str] | None = Field(None, description="JFrog product names")
    stage: (
        Literal["open_source_project", "startup", "feature_of_large_company", "unknown"] | None
    ) = None
    verification: Literal["supported", "partial"] = "supported"
    verification_label: str = Field("Verified", description='"Verified" or "Partly verified"')
    verifier_note: str | None = Field(None, description="What the verifier could not confirm")
    confidence: Literal["high", "medium"] = Field(
        "high", description="high only when supported; partly verified statements are medium"
    )


class RadarMentionOut(CitationRef):
    source: str | None
    origin: str | None


class RadarMergeOut(BaseModel):
    name: str
    rule: str
    direction: Literal["absorbed", "merged_into", "alias"]
    other_id: int | None = None
    at: str | None = None


class RadarCandidateDetail(RadarCandidateOut):
    statements: list[RadarStatementOut]
    dropped_statements: int = Field(description="Profile statements removed by the checks")
    profile_generated_at: datetime | None
    mentions: list[RadarMentionOut]
    merges: list[RadarMergeOut]
    other_names: list[str]
    dismiss_reason: str | None
    dismiss_comment: str | None
    approved_entity: str | None = Field(None, description="Slug of the entity created on approval")


class RadarApproveIn(BaseModel):
    relationship: Literal["competitor", "partner", "coopetition"] = "competitor"
    name: Annotated[str, Field(min_length=2, max_length=100)] | None = None
    type: Literal["company", "product"] | None = Field(
        None, description="Default: company for a company candidate, else product"
    )
    add_sources: bool = Field(
        True, description="Create a (disabled) GitHub releases source for the candidate's repo"
    )
    comment: Annotated[str, Field(max_length=500)] | None = None


class RadarDismissIn(BaseModel):
    reason: DismissReason
    comment: Annotated[str, Field(max_length=500)] | None = None


class RadarWatchIn(BaseModel):
    comment: Annotated[str, Field(max_length=500)] | None = None


class CreatedEntity(BaseModel):
    slug: str
    name: str
    type: EntityType
    relationship: Relationship


class CreatedSource(BaseModel):
    name: str
    url: str
    is_active: bool


class RadarApproveOut(BaseModel):
    candidate: RadarCandidateDetail
    entity: CreatedEntity
    sources: list[CreatedSource]


class RadarTeaser(BaseModel):
    id: int
    name: str
    status: RadarStatusOut
    score: float | None
    sources_count: int
    line: str | None


# ---------- ask your intelligence ----------

AskDocType = Literal["article", "pricing_page", "battlecard", "radar"]
AskFeedbackReason = Literal[
    "inaccurate_summary", "unsupported_claim", "outdated", "not_relevant",
    "missing_information", "other",
]  # fmt: skip


class AskIn(BaseModel):
    question: Annotated[str, Field(min_length=3, max_length=500)]
    conversation_id: int | None = None


class AskSource(BaseModel):
    n: int = Field(description="Citation number shown in the answer")
    id: str = Field(description="Document id: sig:<signal id>, bc:<slug>:<section>:<n>, radar:<id>")
    type: AskDocType
    title: str
    source: str | None
    source_kind: str | None = Field(None, description="official / press / community / github")
    date: datetime | None
    url: str = Field(description="Original article, or the battlecard / radar page")
    confidence: float | None
    confidence_band: str | None


class AskPoint(BaseModel):
    lead: str
    text: str
    citations: list[int] = Field(description="Numbers into `sources`")


class AskMeaning(BaseModel):
    text: str
    citations: list[int]


class AskFilters(BaseModel):
    company: str | None = None
    company_slug: str | None = None
    window: str | None = Field(None, description="e.g. 'last 7 days', 'October 2026'")


class AskAnswerOut(BaseModel):
    turn_id: int
    position: int
    question: str
    standalone_question: str
    filters: AskFilters
    not_enough_evidence: bool
    intro: str | None = None
    points: list[AskPoint]
    meaning_for_jfrog: AskMeaning | None = None
    gap: str | None = None
    suggestions: list[str]
    suggested_followups: list[str]
    sources: list[AskSource] = Field(description="The cited documents, in citation order")
    retrieved: int = Field(description="Documents given to the answer model")
    cost_usd: float | None = Field(None, description="LLM cost of this turn (0 = cache hits)")
    cached: bool = Field(False, description="Every LLM call of this turn came from the cache")
    created_at: datetime


class AskOut(AskAnswerOut):
    conversation_id: int


class AskConversationOut(BaseModel):
    id: int
    title: str
    turns: int
    updated_at: datetime


class AskConversationDetail(BaseModel):
    id: int
    title: str
    turns: list[AskAnswerOut]


class AskFeedbackIn(BaseModel):
    helpful: bool
    reason: AskFeedbackReason | None = None
    comment: Annotated[str, Field(max_length=500)] | None = None


class AskFeedbackOut(BaseModel):
    turn_id: int
    helpful: bool


BriefOut.model_rebuild()


# ---------- analyst review of signals (S14, D68) ----------

SignalReason = Literal[
    "not_relevant",
    "wrong_entity",
    "wrong_category",
    "impact_too_high",
    "impact_too_low",
    "inaccurate_summary",
    "unsupported_claim",
    "duplicate",
    "outdated",
    "other",
]

AnalystName = Annotated[str, Field(min_length=1, max_length=100)]
Comment = Annotated[str, Field(max_length=500)]


class SignalApproveIn(BaseModel):
    analyst: AnalystName = "analyst"
    comment: Comment | None = None


class SignalRejectIn(BaseModel):
    analyst: AnalystName = "analyst"
    reason_code: SignalReason = "not_relevant"
    comment: Comment | None = None


class SignalCorrectIn(BaseModel):
    """Only the fields the analyst changed. relevant=false also rejects the signal."""

    analyst: AnalystName = "analyst"
    reason_code: SignalReason
    comment: Comment | None = None
    category: SignalCategory | None = None
    impact: Annotated[int, Field(ge=1, le=5)] | None = None
    relevant: bool | None = None
    primary_entity: Annotated[str, Field(max_length=100)] | None = Field(
        None, description="Entity slug, or 'none' = about no tracked company"
    )


class ReviewProgress(BaseModel):
    reviewed: int
    total: int


class ReviewQueueOut(BaseModel):
    items: list[SignalOut] = Field(description="Signals waiting for review, impact × confidence")
    progress: ReviewProgress


class FieldRate(BaseModel):
    corrected: int
    rate: float = Field(description="corrected / reviewed (0 if none reviewed)")


class ImpactRate(FieldRate):
    mean_delta: float | None = Field(
        None, description="Mean of analyst − model impact over impact corrections"
    )


class FieldRates(BaseModel):
    category: FieldRate
    impact: ImpactRate
    relevance: FieldRate = Field(
        description="relevant corrected to no, or rejected as not relevant"
    )
    entity: FieldRate


class ReasonCount(BaseModel):
    code: ReasonCode
    count: int


class ScorecardStats(BaseModel):
    reviewed: int
    approved: int = Field(description="Approved as-is")
    corrected: int = Field(description="At least one field corrected (not rejected)")
    rejected: int
    approved_pct: float
    corrected_pct: float
    rejected_pct: float
    fields: FieldRates
    reasons: list[ReasonCount] = Field(description="Most common reason codes, most frequent first")


class ScorecardWeek(ScorecardStats):
    week_start: str = Field(description="Monday of the ISO week (YYYY-MM-DD, UTC)")


class ScorecardOut(BaseModel):
    overall: ScorecardStats
    weeks: list[ScorecardWeek] = Field(description="Newest week first")


# ---------- Research missions (S15) ----------

MissionStatusOut = Literal[
    "queued", "planning", "awaiting_approval", "running", "completed", "failed", "cancelled",
    "budget_exceeded",
]  # fmt: skip
MissionOriginOut = Literal["manual", "signal", "radar_candidate", "capability", "ask_turn"]


class MissionScopeIn(BaseModel):
    company_ids: Annotated[list[int], Field(max_length=5)] = Field(default_factory=list)
    days: Annotated[int, Field(ge=1, le=3650)] | None = None


class MissionCreateIn(BaseModel):
    brief: Annotated[str, Field(min_length=10, max_length=4000)]
    scope: MissionScopeIn = Field(default_factory=MissionScopeIn)
    budget_usd: Annotated[float, Field(gt=0, le=20)] | None = None
    max_steps: Annotated[int, Field(ge=1, le=200)] | None = None
    origin_kind: MissionOriginOut = "manual"
    origin_ref: dict[str, int] = Field(default_factory=dict)
    analyst: Annotated[str, Field(max_length=100)] = ""


class MissionPrefillOut(BaseModel):
    brief: str
    origin_kind: MissionOriginOut
    origin_ref: dict[str, int]
    origin_label: str
    company_ids: list[int]
    days: int | None
    budget_usd: float
    max_steps: int
    max_budget_usd: float
    max_max_steps: int


class SubQuestionOut(BaseModel):
    question: str
    search_query: str
    why: str = ""


class SubQuestionIn(BaseModel):
    question: Annotated[str, Field(min_length=5, max_length=300)]
    search_query: Annotated[str, Field(max_length=200)] = ""
    why: Annotated[str, Field(max_length=300)] = ""


class MissionPlanOut(BaseModel):
    sub_questions: list[SubQuestionOut]
    approved: bool
    approved_by: str | None = None
    edited_by_analyst: bool = False


class MissionPlanIn(BaseModel):
    sub_questions: Annotated[list[SubQuestionIn], Field(min_length=1, max_length=6)] | None = None
    analyst: Annotated[str, Field(max_length=100)] = ""


class MissionStepOut(BaseModel):
    step_no: int
    kind: str
    summary: str
    cost_usd: float
    created_at: datetime
    counted: bool = Field(description="Counts toward max_steps (search / read / extract / verify)")


class MissionSourceOut(BaseModel):
    """A source the agent read: link and title only (the fetched text is never returned)."""

    id: int
    kind: Literal["kb", "web"]
    url: str
    title: str
    signal_id: int | None


class MissionFindingOut(BaseModel):
    id: int
    sub_question_no: int
    statement: str
    evidence_quote: str
    source: MissionSourceOut
    confidence: Literal["high", "medium", "low"]
    verification: Literal["supported", "partial"]
    verification_label: str = Field(description='"Verified" or "Partly verified"')
    verification_notes: str | None
    is_claim: bool
    impact: int | None
    status: Literal["new", "approved", "rejected"]
    signal_id: int | None
    reviewed_by: str | None


class MissionReportPointOut(BaseModel):
    text: str
    finding_ids: list[int]


class MissionReportOut(BaseModel):
    summary: list[MissionReportPointOut]
    open_questions: list[str]
    stopped_reason: str | None
    generated: Literal["llm", "fallback"]


class MissionOut(BaseModel):
    id: int
    title: str
    brief: str
    status: MissionStatusOut
    origin_kind: MissionOriginOut
    origin_ref: dict[str, int]
    origin_label: str
    origin_link: str | None = Field(description="App route of the origin, e.g. /radar/12")
    scope_label: str
    company_ids: list[int]
    days: int | None
    budget_usd: float
    cost_usd: float
    max_steps: int
    steps_used: int
    findings_new: int
    findings_approved: int
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None


class MissionListOut(BaseModel):
    items: list[MissionOut]


class MissionDetailOut(MissionOut):
    plan: MissionPlanOut | None
    steps: list[MissionStepOut]
    findings: list[MissionFindingOut]
    dropped_count: int
    dropped: list[str] = Field(description="Why findings were dropped (short reasons)")
    report: MissionReportOut | None
    radar_candidate_id: int | None
    stop_requested: bool


class FindingReviewIn(BaseModel):
    analyst: Annotated[str, Field(max_length=100)] = ""
    reason: ReasonCode | None = None
    comment: Annotated[str, Field(max_length=500)] | None = None


class FindingReviewOut(BaseModel):
    finding: MissionFindingOut
    signal_id: int | None


class RadarSnapshotOut(BaseModel):
    profile_status: str
    relation: str | None
    score: float | None
    mentions: int
    statements: list[str]


class RadarRefreshOut(BaseModel):
    status: str
    before: RadarSnapshotOut
    after: RadarSnapshotOut
