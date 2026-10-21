"""Load and validate the YAML files in config/ (competitors, sources, capabilities, LLM routing).

Each file maps to a Pydantic model with extra="forbid", so typos fail loudly at load time.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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


class ProductConfig(_Strict):
    slug: Slug
    name: str
    aliases: list[Annotated[str, Field(min_length=2)] | AliasConfig] = Field(default_factory=list)
    # Match rule for the entity's own name (for names that are common words: Socket, Docker).
    match: MatchRule | None = None
    segments: list[Segment] = Field(default_factory=list)
    website: Url | None = None
    description: str | None = None
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


# ---------- llm_routing.yaml ----------


class TaskRoute(_Strict):
    model: Annotated[str, Field(pattern=r"^[\w.-]+/\S+$")]  # LiteLLM "provider/model"
    max_tokens: Annotated[int, Field(gt=0)] = 1024
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.0

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


class PipelineFile(_Strict):
    embedding: EmbedSettings = Field(default_factory=EmbedSettings)
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    llm: LLMStageSettings = Field(default_factory=LLMStageSettings)
    triage: TriageSettings = Field(default_factory=TriageSettings)
    enrich: EnrichSettings = Field(default_factory=EnrichSettings)
    verify: VerifySettings = Field(default_factory=VerifySettings)
    score: ScoreSettings = Field(default_factory=ScoreSettings)


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
def _pipeline(config_dir: Path) -> PipelineFile:
    name = "pipeline.yaml"
    return _validate(PipelineFile, config_dir, name, _read_yaml(config_dir, name))


def load_pipeline(config_dir: Path | None = None) -> PipelineFile:
    return _pipeline(_dir(config_dir))


def load_competitors(config_dir: Path | None = None) -> CompetitorsFile:
    return _competitors(_dir(config_dir))


def load_sources(config_dir: Path | None = None) -> SourcesFile:
    return _sources(_dir(config_dir))


def load_capabilities(config_dir: Path | None = None) -> CapabilitiesFile:
    return _capabilities(_dir(config_dir))


def load_llm_routing(config_dir: Path | None = None) -> LLMRoutingFile:
    return _llm_routing(_dir(config_dir))


def clear_cache() -> None:
    for fn in (_competitors, _sources, _capabilities, _llm_routing, _pipeline):
        fn.cache_clear()
