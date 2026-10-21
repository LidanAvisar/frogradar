"""Enums shared by the DB models and the config schemas.

Stored in Postgres as VARCHAR + CHECK constraint, not native PG enums
(see README "Design decisions").
"""

from enum import StrEnum


class EntityType(StrEnum):
    COMPANY = "company"
    PRODUCT = "product"


class Relationship(StrEnum):
    SELF = "self"
    COMPETITOR = "competitor"
    PARTNER = "partner"
    COOPETITION = "coopetition"


class EntityReviewStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"


class Segment(StrEnum):
    ARTIFACT_MANAGEMENT = "artifact_management"
    SUPPLY_CHAIN_SECURITY = "supply_chain_security"
    MLOPS = "mlops"
    CICD = "cicd"
    DISTRIBUTION = "distribution"


class DiscoveredBy(StrEnum):
    CONFIG = "config"
    RADAR = "radar"
    MISSION = "mission"
    ANALYST = "analyst"


class SourceKind(StrEnum):
    OFFICIAL = "official"
    PRESS = "press"
    COMMUNITY = "community"
    GITHUB = "github"


class FetchMethod(StrEnum):
    RSS = "rss"
    GITHUB = "github"
    PAGE_DIFF = "page_diff"
    WEB = "web"
    # Discovery sources (config/discovery.yaml, Competitor Radar):
    HN_SEARCH = "hn_search"
    GITHUB_SEARCH = "github_search"
    CNCF_LANDSCAPE = "cncf_landscape"


DISCOVERY_METHODS = frozenset(
    {FetchMethod.HN_SEARCH, FetchMethod.GITHUB_SEARCH, FetchMethod.CNCF_LANDSCAPE}
)


class SignalCategory(StrEnum):
    PRODUCT_LAUNCH = "product_launch"
    FEATURE_UPDATE = "feature_update"
    PRICING_CHANGE = "pricing_change"
    PARTNERSHIP = "partnership"
    ACQUISITION = "acquisition"
    FUNDING = "funding"
    SECURITY_INCIDENT = "security_incident"
    LEADERSHIP_CHANGE = "leadership_change"
    CUSTOMER_WIN = "customer_win"
    OPEN_SOURCE = "open_source"
    EVENT = "event"
    ANALYST_REPORT = "analyst_report"
    OTHER = "other"


class Verification(StrEnum):
    PENDING = "pending"
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class ReviewStatus(StrEnum):
    NEW = "new"
    APPROVED = "approved"
    REJECTED = "rejected"


class InsightType(StrEnum):
    THREAT = "threat"
    OPPORTUNITY = "opportunity"
    BLIND_SPOT = "blind_spot"
    TREND = "trend"


class FeedbackTarget(StrEnum):
    SIGNAL = "signal"
    INSIGHT = "insight"
    CAPABILITY_MATRIX = "capability_matrix"
    MISSION = "mission"
    RADAR_CANDIDATE = "radar_candidate"
    ASK_TURN = "ask_turn"


class FeedbackAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    RATE = "rate"
    WATCH = "watch"


class ReasonCode(StrEnum):
    NOT_RELEVANT = "not_relevant"
    WRONG_ENTITY = "wrong_entity"
    WRONG_CATEGORY = "wrong_category"
    IMPACT_TOO_HIGH = "impact_too_high"
    IMPACT_TOO_LOW = "impact_too_low"
    INACCURATE_SUMMARY = "inaccurate_summary"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    DUPLICATE = "duplicate"
    OUTDATED = "outdated"
    # Radar dismissals (with not_relevant, duplicate, other):
    NOT_A_COMPETITOR = "not_a_competitor"
    TOO_SMALL = "too_small"
    ALREADY_TRACKED = "already_tracked"
    # Ask answers ("Something's wrong", with inaccurate_summary, unsupported_claim, outdated, ...):
    MISSING_INFORMATION = "missing_information"
    OTHER = "other"


class RadarKind(StrEnum):
    COMPANY = "company"
    PRODUCT = "product"
    OPEN_SOURCE_PROJECT = "open_source_project"


class RadarStatus(StrEnum):
    NEW = "new"
    WATCHING = "watching"
    APPROVED = "approved"
    DISMISSED = "dismissed"


class ProfileStatus(StrEnum):
    NONE = "none"
    OK = "ok"
    REJECTED = "rejected"


class MissionStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"


class MissionStepKind(StrEnum):
    PLAN = "plan"
    SEARCH = "search"
    FETCH = "fetch"
    READ = "read"
    ANALYZE = "analyze"
    SIGNAL = "signal"
    REPORT = "report"
    ERROR = "error"


class CapabilityLevel(StrEnum):
    UNKNOWN = "unknown"
    NONE = "none"
    PLANNED = "planned"
    PARTIAL = "partial"
    FULL = "full"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class RunTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    BACKFILL = "backfill"


class ReportKind(StrEnum):
    DAILY_BRIEF = "daily_brief"
    BATTLECARD = "battlecard"
    DIGEST = "digest"
    MISSION_REPORT = "mission_report"
