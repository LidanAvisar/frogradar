"""Load and validate the YAML files in config/ (competitors, sources, capabilities, LLM routing,
pipeline, discovery).

Each file maps to a Pydantic model with extra="forbid", so typos fail loudly at load time.
"""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.core.config import get_settings
from app.db.enums import FetchMethod, Relationship, Segment, SourceKind

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=100)]
Url = Annotated[str, Field(pattern=r"^https?://\S+$")]

# Used when a source doesn't set trust_score explicitly.
DEFAULT_TRUST: dict[SourceKind, float] = {
    SourceKind.OFFICIAL: 0.9,
    SourceKind.GITHUB: 0.85,
    SourceKind.PRESS: 0.7,
    SourceKind.COMMUNITY: 0.5,
}

# pgvector cannot build an HNSW index on `vector` columns above 2000 dimensions.
MAX_HNSW_DIMENSIONS = 2000


class ConfigError(RuntimeError):
    """A config file is missing, unparsable, or fails validation."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------- competitors.yaml / sources.yaml ----------


class SourceConfig(_Strict):
    name: str
    url: Url
    kind: SourceKind
    fetch_method: FetchMethod
    trust_score: Annotated[float, Field(ge=0, le=1)] | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="after")
    def _check_options(self) -> Self:
        if self.fetch_method is FetchMethod.GITHUB:
            repos = self.options.get("repos")
            if not (isinstance(repos, list) and repos and all(isinstance(r, str) for r in repos)):
                raise ValueError(f"github source {self.name!r} needs options.repos: [repo, ...]")
        if not isinstance(self.options.get("keep_fragment", False), bool):
            raise ValueError(f"source {self.name!r}: options.keep_fragment must be true/false")
        return self

    @property
    def effective_trust(self) -> float:
        return self.trust_score if self.trust_score is not None else DEFAULT_TRUST[self.kind]


class MatchRule(_Strict):
    """How strict entity linking is for one name/alias (app.pipeline.link).

    A match counts if it is in the title (and title_suffices), or if the term occurs at least
    min_mentions times in title + text. Word boundaries always apply. enabled=false turns the term
    off (company names that are too broad, like Microsoft: linked through products only, D13).
    """

    enabled: bool = True
    case_sensitive: bool = False
    min_mentions: Annotated[int, Field(ge=1, le=50)] = 1
    title_suffices: bool = True

    @property
    def is_default(self) -> bool:
        return self == MatchRule()


class AliasConfig(MatchRule):
    """An alias with its own match rule, e.g. {alias: GHCR, case_sensitive: true}."""

    alias: Annotated[str, Field(min_length=2)]


class Discontinued(_Strict):
    """End of life of a product: no longer sold or supported."""

    end_of_life: date
    source: Url


class ProductConfig(_Strict):
    slug: Slug
    name: str
    aliases: list[Annotated[str, Field(min_length=2)] | AliasConfig] = Field(default_factory=list)
    # Match rule for the entity's own name (for names that are common words: Socket, Docker).
    match: MatchRule | None = None
    segments: list[Segment] = Field(default_factory=list)
    website: Url | None = None
    description: str | None = None
    discontinued: Discontinued | None = None
    sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_aliases(self) -> Self:
        _assert_unique(f"alias of {self.slug!r}", [t.casefold() for t in self.match_terms])
        return self

    @property
    def alias_names(self) -> list[str]:
        return [a if isinstance(a, str) else a.alias for a in self.aliases]

    @property
    def match_terms(self) -> list[str]:
        """Name first, then aliases: every string entity linking looks for."""
        return [self.name, *self.alias_names]

    @property
    def match_rules(self) -> dict[str, dict[str, Any]]:
        """Non-default rules by term, as stored in entities.match_rules."""
        rules: dict[str, MatchRule] = {}
        if self.match is not None:
            rules[self.name] = self.match
        for a in self.aliases:
            if isinstance(a, AliasConfig):
                rules[a.alias] = MatchRule(**a.model_dump(exclude={"alias"}))
        return {t: r.model_dump() for t, r in rules.items() if not r.is_default}


class CompanyConfig(ProductConfig):
    relationship: Relationship
    is_tracked: bool = True
    products: list[ProductConfig] = Field(default_factory=list)


class CompetitorsFile(_Strict):
    companies: list[CompanyConfig]

    @model_validator(mode="after")
    def _check(self) -> Self:
        selves = [c.slug for c in self.companies if c.relationship is Relationship.SELF]
        if len(selves) != 1:
            raise ValueError(f"exactly one company must have relationship 'self', found {selves}")
        _assert_unique("slug", [s for c in self.companies for s in (c.slug, *_product_slugs(c))])
        _assert_unique("source url", [s.url for s in self.all_sources()])
        return self

    def all_sources(self) -> list[SourceConfig]:
        return [s for c in self.companies for owner in (c, *c.products) for s in owner.sources]


class SourcesFile(_Strict):
    """Industry sources that are not tied to one competitor."""

    sources: list[SourceConfig]

    @model_validator(mode="after")
    def _check(self) -> Self:
        _assert_unique("source url", [s.url for s in self.sources])
        return self


# ---------- capabilities.yaml ----------


class CapabilityConfig(_Strict):
    slug: Slug
    name: str
    description: str


class CapabilitiesFile(_Strict):
    segments: dict[Segment, list[CapabilityConfig]]

    @model_validator(mode="after")
    def _check(self) -> Self:
        _assert_unique("capability slug", [c.slug for caps in self.segments.values() for c in caps])
        return self


# ---------- jfrog_capabilities.yaml ----------


class SelfCapability(_Strict):
    level: Literal["full", "partial", "planned", "none", "unknown"]
    note: Annotated[str, Field(min_length=3, max_length=240)]
    source: Annotated[str, Field(pattern=r"^https://")] | None = None

    @model_validator(mode="after")
    def _source_for_known_levels(self) -> Self:
        if self.level != "unknown" and not self.source:
            raise ValueError("a known level needs a source (public JFrog doc URL)")
        return self


class SelfCapabilitiesFile(_Strict):
    """JFrog's own capability levels (from public docs, not from the LLM). Every capability slug in
    capabilities.yaml must be listed (unknown is a valid level)."""

    capabilities: dict[str, SelfCapability]


def check_self_capabilities(levels: SelfCapabilitiesFile, caps: CapabilitiesFile) -> None:
    known = {c.slug for group in caps.segments.values() for c in group}
    listed = set(levels.capabilities)
    if listed - known:
        raise ConfigError(f"jfrog_capabilities.yaml: unknown slugs {sorted(listed - known)}")
    if known - listed:
        raise ConfigError(f"jfrog_capabilities.yaml: missing slugs {sorted(known - listed)}")


# ---------- llm_routing.yaml ----------


def _yaml_off(value: object) -> object:
    # YAML 1.1 reads a bare `off` as false; accept it rather than fail on a quoting detail.
    return "off" if value is False else value


Thinking = Annotated[Literal["default", "off", "low", "medium", "high"], BeforeValidator(_yaml_off)]


class TaskRoute(_Strict):
    model: Annotated[str, Field(pattern=r"^[\w.-]+/\S+$")]  # LiteLLM "provider/model"
    max_tokens: Annotated[int, Field(gt=0)] = 1024
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.0
    # Model reasoning ("thinking"): default = send nothing (the model's own default), off =
    # explicitly disabled, low/medium/high = reasoning effort. Mapped per provider in
    # app.core.llm.provider.thinking_params; part of the LLM cache key.
    thinking: Thinking = "default"

    @property
    def provider(self) -> str:
        return self.model.split("/", 1)[0]


class TaskRoutes(_Strict):
    bulk: TaskRoute
    synthesis: TaskRoute
    verify: TaskRoute


class EmbeddingRoute(_Strict):
    model: Annotated[str, Field(pattern=r"^[\w.-]+/\S+$")]
    dimensions: Annotated[int, Field(gt=0, le=MAX_HNSW_DIMENSIONS)]


class ModelPricing(_Strict):
    input_per_mtok_usd: Annotated[float, Field(ge=0)]
    output_per_mtok_usd: Annotated[float, Field(ge=0)] = 0.0


class LLMRoutingFile(_Strict):
    tasks: TaskRoutes
    embedding: EmbeddingRoute
    # Fallback prices for models LiteLLM doesn't know (USD per 1M tokens).
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        verify = self.tasks.verify.provider
        writers = {self.tasks.bulk.provider, self.tasks.synthesis.provider}
        if verify in writers:
            raise ValueError(
                f"verify provider '{verify}' must differ from the bulk and synthesis "
                f"providers {sorted(writers)}"
            )
        return self


# ---------- pipeline.yaml ----------

Similarity = Annotated[float, Field(gt=0, le=1)]


class EmbedSettings(_Strict):
    # Characters of raw_text after the title that go into the embedding input.
    max_chars: Annotated[int, Field(ge=200, le=30000)] = 2000
    batch_size: Annotated[int, Field(ge=1, le=2048)] = 64


class DedupSettings(_Strict):
    window_days: Annotated[int, Field(ge=1, le=365)] = 14
    # Cosine similarity a signal needs to the cluster's canonical signal to join it.
    cross_source_threshold: Similarity = 0.85
    # Same-source pairs (templated changelog entries look alike) need much more.
    same_source_threshold: Similarity = 0.97
    neighbours: Annotated[int, Field(ge=1, le=200)] = 10

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.same_source_threshold < self.cross_source_threshold:
            raise ValueError("same_source_threshold must be >= cross_source_threshold")
        return self


class LLMStageSettings(_Strict):
    # Parallel LLM calls per stage (bounded thread pool).
    max_workers: Annotated[int, Field(ge=1, le=32)] = 4


class TriageSettings(_Strict):
    max_chars: Annotated[int, Field(ge=500, le=50000)] = 3000


class EnrichSettings(_Strict):
    max_chars: Annotated[int, Field(ge=1000, le=100000)] = 12000
    # Extra attempts when the evidence quote is not verbatim in raw_text.
    quote_retries: Annotated[int, Field(ge=0, le=3)] = 1
    # Near-miss quotes are replaced by the real source span (one or two adjacent sentences) when
    # both similarity and coverage reach this (D39); otherwise the retry runs.
    quote_repair_threshold: Similarity = 0.9


class VerifySettings(_Strict):
    max_chars: Annotated[int, Field(ge=1000, le=100000)] = 12000


Unit = Annotated[float, Field(ge=0, le=1)]


class ScoreWeights(_Strict):
    verification: Unit = 0.45
    trust: Unit = 0.25
    corroboration: Unit = 0.15
    evidence: Unit = 0.15

    @model_validator(mode="after")
    def _check(self) -> Self:
        total = self.verification + self.trust + self.corroboration + self.evidence
        if abs(total - 1) > 1e-6:
            raise ValueError(f"score weights must sum to 1, got {total:g}")
        return self


class VerificationValues(_Strict):
    supported: Unit = 1.0
    partial: Unit = 0.5
    unsupported: Unit = 0.0


class ScoreCaps(_Strict):
    unsupported: Unit = 0.30
    quote_unverified: Unit = 0.60


class ScoreBands(_Strict):
    high: Unit = 0.75
    medium: Unit = 0.50

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.medium >= self.high:
            raise ValueError("bands: medium must be below high")
        return self


class ScoreSettings(_Strict):
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    verification_values: VerificationValues = Field(default_factory=VerificationValues)
    # Sources in the dedup cluster needed for full corroboration credit (1 source = 0).
    corroboration_full_at: Annotated[int, Field(ge=2, le=20)] = 3
    no_source_trust: Unit = 0.5  # signals without a source (research findings)
    caps: ScoreCaps = Field(default_factory=ScoreCaps)
    bands: ScoreBands = Field(default_factory=ScoreBands)


class WaybackSettings(_Strict):
    cdx_url: str = "https://web.archive.org/cdx/search/cdx"
    snapshot_url: str = "https://web.archive.org/web/{timestamp}id_/{url}"
    target_days_ago: list[Annotated[int, Field(ge=1, le=3650)]] = Field(
        default_factory=lambda: [365, 182, 91]
    )
    window_days: Annotated[int, Field(ge=1, le=180)] = 31  # max distance from a target date
    host_delay_s: Annotated[float, Field(ge=1, le=60)] = 5.0  # web.archive.org politeness
    # An archived page whose text is much shorter than today's probably didn't render (JS).
    min_length_ratio: Unit = 0.5


class PageDiffSettings(_Strict):
    min_text_chars: Annotated[int, Field(ge=50, le=100000)] = 500  # less → JS shell, no snapshot
    short_line_chars: Annotated[int, Field(ge=0, le=200)] = 30  # runs of short lines are joined
    min_changed_chars: Annotated[int, Field(ge=0, le=100000)] = 40  # smaller diffs are noise
    restructure_ratio: Unit = 0.5  # changed-line share above which the page counts as redesigned
    context_lines: Annotated[int, Field(ge=0, le=5)] = 1
    max_diff_chars: Annotated[int, Field(ge=1000, le=100000)] = 12000
    noise_patterns: list[str] = Field(default_factory=list)  # removed from lines
    drop_line_patterns: list[str] = Field(default_factory=list)  # whole lines removed
    price_patterns: list[str] = Field(default_factory=list)  # lines that state a price/limit
    wayback: WaybackSettings = Field(default_factory=WaybackSettings)

    @model_validator(mode="after")
    def _check(self) -> Self:
        for pattern in (*self.noise_patterns, *self.drop_line_patterns, *self.price_patterns):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"bad regex {pattern!r}: {e}") from None
        return self


class BriefSettings(_Strict):
    window_days: Annotated[int, Field(ge=1, le=90)] = 7
    max_signals: Annotated[int, Field(ge=1, le=50)] = 12


class BattlecardSettings(_Strict):
    refresh_days: Annotated[int, Field(ge=1, le=90)] = 7
    trigger_impact: Annotated[int, Field(ge=1, le=5)] = 4
    lookback_days: Annotated[int, Field(ge=7, le=730)] = 180
    max_signals: Annotated[int, Field(ge=1, le=100)] = 30
    min_primary_signals: Annotated[int, Field(ge=1, le=50)] = 1
    changed_recently_days: Annotated[int, Field(ge=1, le=90)] = 14


class SynthesisSettings(_Strict):
    brief: BriefSettings = Field(default_factory=BriefSettings)
    battlecards: BattlecardSettings = Field(default_factory=BattlecardSettings)


class AskSettings(_Strict):
    """Ask your intelligence (S13, D65): hybrid retrieval + checked answer."""

    k: Annotated[int, Field(ge=1, le=30)] = 8  # documents given to the answer model
    max_docs: Annotated[int, Field(ge=1, le=30)] = 12  # hard per-request cap (k is clipped to it)
    candidates: Annotated[int, Field(ge=1, le=100)] = 20  # per ranked list before fusion
    rrf_k: Annotated[int, Field(ge=1, le=1000)] = 60  # reciprocal rank fusion constant
    # Vector hits below this cosine similarity are not candidates (full-text hits still are).
    min_cosine: Annotated[float, Field(ge=-1, le=1)] = 0.25
    history_turns: Annotated[int, Field(ge=0, le=10)] = 3  # turns given to the follow-up rewrite
    question_max_chars: Annotated[int, Field(ge=10, le=2000)] = 500
    doc_max_chars: Annotated[int, Field(ge=200, le=5000)] = 1200  # one document block

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.k > self.max_docs:
            raise ValueError(f"ask.k ({self.k}) must not exceed ask.max_docs ({self.max_docs})")
        return self


class PipelineFile(_Strict):
    embedding: EmbedSettings = Field(default_factory=EmbedSettings)
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    llm: LLMStageSettings = Field(default_factory=LLMStageSettings)
    triage: TriageSettings = Field(default_factory=TriageSettings)
    enrich: EnrichSettings = Field(default_factory=EnrichSettings)
    verify: VerifySettings = Field(default_factory=VerifySettings)
    score: ScoreSettings = Field(default_factory=ScoreSettings)
    page_diff: PageDiffSettings = Field(default_factory=PageDiffSettings)
    synthesis: SynthesisSettings = Field(default_factory=SynthesisSettings)
    ask: AskSettings = Field(default_factory=AskSettings)


# ---------- discovery.yaml (Competitor Radar) ----------

DISCOVERY_FETCH = frozenset(
    {FetchMethod.HN_SEARCH, FetchMethod.GITHUB_SEARCH, FetchMethod.CNCF_LANDSCAPE}
)


class HNQuerySet(_Strict):
    min_points: Annotated[int, Field(ge=0, le=10000)]
    queries: list[Annotated[str, Field(min_length=2, max_length=100)]] = Field(default_factory=list)


class HNSettings(_Strict):
    window_days: Annotated[int, Field(ge=1, le=365)] = 90
    # Show HN posts: all of them that match one of the queries (or all, if match_queries is false).
    show_hn: HNQuerySet = Field(default_factory=lambda: HNQuerySet(min_points=10))
    show_hn_match_queries: bool = True
    keywords: HNQuerySet = Field(default_factory=lambda: HNQuerySet(min_points=30))
    hits_per_page: Annotated[int, Field(ge=10, le=1000)] = 100
    max_pages: Annotated[int, Field(ge=1, le=50)] = 5
    fetch_linked_page: bool = True
    linked_page_chars: Annotated[int, Field(ge=500, le=50000)] = 8000


class GitHubSearchSettings(_Strict):
    created_within_days: Annotated[int, Field(ge=1, le=365)] = 90
    min_stars: Annotated[int, Field(ge=0, le=100000)] = 50
    queries: list[Annotated[str, Field(min_length=2, max_length=200)]]
    per_page: Annotated[int, Field(ge=10, le=100)] = 100
    max_pages: Annotated[int, Field(ge=1, le=10)] = 2
    # Seconds between search requests (unauthenticated limit: 10/min; with a token: 30/min).
    min_interval_s: Annotated[float, Field(ge=0, le=60)] = 6.5
    readme_chars: Annotated[int, Field(ge=0, le=50000)] = 6000
    # Noise filter (code, before triage): case-insensitive regexes on the repo name / description.
    skip_forks: bool = True
    skip_archived: bool = True
    skip_name_patterns: dict[str, str] = Field(default_factory=dict)  # reason -> regex
    skip_description_patterns: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        for pattern in (
            *self.skip_name_patterns.values(),
            *self.skip_description_patterns.values(),
        ):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"bad regex {pattern!r}: {e}") from None
        return self


class CNCFSettings(_Strict):
    # "Category > Subcategory" exactly as in landscape.yml.
    categories: list[str]
    history_days: list[Annotated[int, Field(ge=1, le=730)]] = Field(
        default_factory=lambda: [90, 60, 30]
    )
    commits_api: str = "https://api.github.com/repos/cncf/landscape/commits"
    raw_at_commit: str = "https://raw.githubusercontent.com/cncf/landscape/{sha}/landscape.yml"


class RadarScoreWeights(_Strict):
    # radar_score.v2 (S11 tuning): relevance to JFrog first, popularity second.
    relevance: Unit = 0.35
    mentions: Unit = 0.10
    source_diversity: Unit = 0.20
    mention_velocity: Unit = 0.10
    traction: Unit = 0.10
    recency: Unit = 0.15

    @model_validator(mode="after")
    def _check(self) -> Self:
        total = sum(self.model_dump().values())
        if abs(total - 1) > 1e-6:
            raise ValueError(f"radar score weights must sum to 1, got {total:g}")
        return self


class RelationWeights(_Strict):
    threatens: Unit = 1.0
    both: Unit = 1.0
    complements: Unit = 0.6
    none: Unit = 0.05


class SourceCredibility(_Strict):
    """Weight of one mention / one source by source kind; discovery sources (HN, GitHub search,
    CNCF) use `discovery` whatever their kind."""

    official: Unit = 1.0
    press: Unit = 1.0
    community: Unit = 0.5
    github: Unit = 0.5
    discovery: Unit = 0.35
    no_source: Unit = 0.5


class RadarScoreSettings(_Strict):
    weights: RadarScoreWeights = Field(default_factory=RadarScoreWeights)
    # Relevance = max enrich impact of the relevant mentions, (impact - 1) / 4. With a profile
    # whose JFrog relation is kept (or only "partial" for a detail), relevance =
    # relation_share x relation weight + (1 - relation_share) x impact factor.
    relation_weights: RelationWeights = Field(default_factory=RelationWeights)
    relation_share: Unit = 0.7
    credibility: SourceCredibility = Field(default_factory=SourceCredibility)
    mentions_full_at: Annotated[float, Field(gt=1, le=1000)] = 6  # credibility-weighted mentions
    sources_full_at: Annotated[float, Field(gt=0, le=50)] = 2  # credibility-weighted sources
    velocity_recent_days: Annotated[int, Field(ge=1, le=90)] = 14
    velocity_window_days: Annotated[int, Field(ge=15, le=730)] = 90
    # Full velocity credit needs at least this many recent mentions (one new mention is not a
    # trend): factor = recent share x min(1, recent / velocity_recent_full_at).
    velocity_recent_full_at: Annotated[int, Field(ge=1, le=100)] = 3
    stars_per_day_full_at: Annotated[float, Field(gt=0, le=10000)] = 50
    hn_points_full_at: Annotated[int, Field(ge=1, le=100000)] = 500
    # One viral HN post can give at most this much of the traction factor.
    hn_max_factor: Unit = 0.5
    recency_half_life_days: Annotated[float, Field(gt=0, le=365)] = 14


class RadarMergeSettings(_Strict):
    # A fuzzy merge needs BOTH: name similarity (difflib ratio of normalized keys) and the cosine
    # similarity of the name embeddings (name + kind + context).
    name_ratio: Similarity = 0.92
    embedding_cosine: Similarity = 0.90


class RadarProfileSettings(_Strict):
    min_score: Annotated[float, Field(ge=0, le=100)] = 35
    top_n: Annotated[int, Field(ge=0, le=100)] = 10
    max_signals: Annotated[int, Field(ge=1, le=20)] = 8
    excerpt_chars: Annotated[int, Field(ge=200, le=10000)] = 1500


class RadarUiSettings(_Strict):
    # Radar page: candidates scoring below this (or whose profile says relation "none") are listed
    # under "Lower relevance" instead of with the top.
    lower_relevance_below: Annotated[float, Field(ge=0, le=100)] = 35


class RadarSettings(_Strict):
    # Names that never become candidates (registries, foundations, AI labs, generic tools).
    ignore: list[str] = Field(default_factory=list)
    merge: RadarMergeSettings = Field(default_factory=RadarMergeSettings)
    score: RadarScoreSettings = Field(default_factory=RadarScoreSettings)
    profile: RadarProfileSettings = Field(default_factory=RadarProfileSettings)
    teaser_count: Annotated[int, Field(ge=1, le=10)] = 3
    ui: RadarUiSettings = Field(default_factory=RadarUiSettings)


class DiscoveryFile(_Strict):
    sources: list[SourceConfig]
    hn: HNSettings = Field(default_factory=HNSettings)
    github: GitHubSearchSettings
    cncf: CNCFSettings
    radar: RadarSettings = Field(default_factory=RadarSettings)

    @model_validator(mode="after")
    def _check(self) -> Self:
        _assert_unique("source url", [s.url for s in self.sources])
        methods = [s.fetch_method for s in self.sources]
        bad = [m.value for m in methods if m not in DISCOVERY_FETCH]
        if bad:
            raise ValueError(f"discovery sources must use a discovery fetch method, got {bad}")
        _assert_unique("discovery fetch_method", [m.value for m in methods])
        return self

    def source_for(self, method: FetchMethod) -> SourceConfig | None:
        return next((s for s in self.sources if s.fetch_method is method), None)


# ---------- loading ----------


def _product_slugs(company: CompanyConfig) -> list[str]:
    return [p.slug for p in company.products]


def _assert_unique(what: str, values: list[str]) -> None:
    seen: set[str] = set()
    dupes = sorted({v for v in values if v in seen or seen.add(v)})
    if dupes:
        raise ValueError(f"duplicate {what}: {dupes}")


def _read_yaml(config_dir: Path, filename: str) -> Any:
    path = config_dir / filename
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"{path}: {e}") from e


def _validate[M: BaseModel](model: type[M], config_dir: Path, filename: str, data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"{config_dir / filename}: {e}") from e


def _dir(config_dir: Path | None) -> Path:
    return config_dir or get_settings().config_dir


@lru_cache
def _competitors(config_dir: Path) -> CompetitorsFile:
    name = "competitors.yaml"
    return _validate(CompetitorsFile, config_dir, name, _read_yaml(config_dir, name))


@lru_cache
def _sources(config_dir: Path) -> SourcesFile:
    name = "sources.yaml"
    return _validate(SourcesFile, config_dir, name, _read_yaml(config_dir, name))


@lru_cache
def _capabilities(config_dir: Path) -> CapabilitiesFile:
    name = "capabilities.yaml"
    return _validate(CapabilitiesFile, config_dir, name, _read_yaml(config_dir, name))


@lru_cache
def _llm_routing(config_dir: Path) -> LLMRoutingFile:
    name = "llm_routing.yaml"
    data = _read_yaml(config_dir, name)
    # Env vars (LLM_MODEL_*, EMBEDDING_MODEL) override the YAML, then the result is validated.
    s = get_settings()
    if isinstance(data, dict):
        tasks = data.get("tasks") or {}
        for task, override in (
            ("bulk", s.llm_model_bulk),
            ("synthesis", s.llm_model_synthesis),
            ("verify", s.llm_model_verify),
        ):
            if override and isinstance(tasks.get(task), dict):
                tasks[task]["model"] = override
        if s.embedding_model and isinstance(data.get("embedding"), dict):
            data["embedding"]["model"] = s.embedding_model
    return _validate(LLMRoutingFile, config_dir, name, data)


@lru_cache
def _self_capabilities(config_dir: Path) -> SelfCapabilitiesFile:
    name = "jfrog_capabilities.yaml"
    levels = _validate(SelfCapabilitiesFile, config_dir, name, _read_yaml(config_dir, name))
    check_self_capabilities(levels, _capabilities(config_dir))
    return levels


@lru_cache
def _discovery(config_dir: Path) -> DiscoveryFile:
    name = "discovery.yaml"
    return _validate(DiscoveryFile, config_dir, name, _read_yaml(config_dir, name))


@lru_cache
def _pipeline(config_dir: Path) -> PipelineFile:
    name = "pipeline.yaml"
    return _validate(PipelineFile, config_dir, name, _read_yaml(config_dir, name))


def load_pipeline(config_dir: Path | None = None) -> PipelineFile:
    return _pipeline(_dir(config_dir))


def load_discovery(config_dir: Path | None = None) -> DiscoveryFile:
    return _discovery(_dir(config_dir))


def load_competitors(config_dir: Path | None = None) -> CompetitorsFile:
    return _competitors(_dir(config_dir))


def load_sources(config_dir: Path | None = None) -> SourcesFile:
    return _sources(_dir(config_dir))


def load_capabilities(config_dir: Path | None = None) -> CapabilitiesFile:
    return _capabilities(_dir(config_dir))


def load_self_capabilities(config_dir: Path | None = None) -> SelfCapabilitiesFile:
    return _self_capabilities(_dir(config_dir))


def load_llm_routing(config_dir: Path | None = None) -> LLMRoutingFile:
    return _llm_routing(_dir(config_dir))


def clear_cache() -> None:
    for fn in (
        _competitors,
        _sources,
        _capabilities,
        _self_capabilities,
        _llm_routing,
        _pipeline,
        _discovery,
    ):
        fn.cache_clear()
