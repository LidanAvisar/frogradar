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


class ProductConfig(_Strict):
    slug: Slug
    name: str
    aliases: list[str] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    website: Url | None = None
    description: str | None = None
    sources: list[SourceConfig] = Field(default_factory=list)


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


def load_competitors(config_dir: Path | None = None) -> CompetitorsFile:
    return _competitors(_dir(config_dir))


def load_sources(config_dir: Path | None = None) -> SourcesFile:
    return _sources(_dir(config_dir))


def load_capabilities(config_dir: Path | None = None) -> CapabilitiesFile:
    return _capabilities(_dir(config_dir))


def load_llm_routing(config_dir: Path | None = None) -> LLMRoutingFile:
    return _llm_routing(_dir(config_dir))


def clear_cache() -> None:
    for fn in (_competitors, _sources, _capabilities, _llm_routing):
        fn.cache_clear()
