"""Pydantic output schemas for the LLM stages (triage, enrich, verify).

Every LLM call returns one of these, validated by the gateway (with retries on validation errors).
The enrich schema is built per run from the DB, so entity and capability slugs are enforced by
validation: a slug the model invents fails validation instead of reaching the DB.

Non-critical explanation fields (triage reason, impact rationale, verify notes, unsupported-claim
items) are soft-limited: an over-long value is cut to the limit with "…" instead of failing
validation, so a wordy note never costs a retry or loses an otherwise valid answer. The limit
stays in the JSON schema as a hint to the model. Analyst-facing content (summary, why JFrog cares,
evidence quote) keeps hard limits.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model, model_validator

from app.db.enums import Segment, SignalCategory

ELLIPSIS = "…"


def _truncate(limit: int) -> Callable[[object], object]:
    def cut(value: object) -> object:
        if isinstance(value, str) and len(value) > limit:
            return value[: limit - 1].rstrip() + ELLIPSIS
        return value

    return cut


def soft_text(limit: int) -> object:
    """str with max_length=limit in the schema, truncated (with …) instead of rejected."""
    return Annotated[str, BeforeValidator(_truncate(limit)), Field(max_length=limit)]


Short = soft_text(300)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- triage ----------


class TriageResult(_Out):
    relevant: bool = Field(description="Does this touch JFrog's market? If unsure: true.")
    reason: Short = Field(description="One sentence: why (not) relevant.")
    segments: list[Segment] = Field(
        default_factory=list, max_length=5, description="JFrog segments touched; [] if irrelevant."
    )

    @model_validator(mode="after")
    def _segments_when_relevant(self) -> Self:
        if self.relevant and not self.segments:
            raise ValueError("relevant signals need at least one segment")
        return self


# ---------- enrich ----------


class NewEntity(_Out):
    """A company/product in the text that we don't track yet (for the Competitor Radar)."""

    name: Annotated[str, Field(min_length=2, max_length=100)]
    kind: Literal["company", "product"]
    context: Annotated[str, Field(max_length=200)] = Field(
        description="Why it matters to JFrog, from the text."
    )


TAKEAWAY_MAX = 160  # hard limit; the prompt asks for <= 140 (one validation retry beyond 160)
DISPLAY_TITLE_MAX = 90  # hard limit; the prompt asks for <= 80


class ProposedPriceChange(_Out):
    """A price change the model read from a page diff. Checked in code (price_verify) before use."""

    label: Annotated[str, Field(max_length=80)] = Field(
        description="Plan / edition / section the price belongs to."
    )
    before: Annotated[str, Field(min_length=1, max_length=60)] = Field(
        description="Old price exactly as written in a [before] line."
    )
    after: Annotated[str, Field(min_length=1, max_length=60)] = Field(
        description="New price exactly as written in the [after] line that replaced it."
    )


class EnrichedSignalBase(_Out):
    """Fields shared by every run's enrich schema (entities/capabilities are added per run)."""

    category: SignalCategory
    summary: Annotated[str, Field(min_length=40, max_length=600)] = Field(
        description="2-3 sentences, facts from the source only."
    )
    why_jfrog_cares: Annotated[str, Field(min_length=40, max_length=600)] = Field(
        description="Specific to JFrog's products and competitive position."
    )
    impact: Annotated[int, Field(ge=1, le=5)]
    impact_rationale: Short = Field(description="Which rubric level applies and why.")
    evidence_quote: Annotated[str, Field(min_length=10, max_length=400)] = Field(
        description="1-2 sentences copied exactly from the source text."
    )
    takeaway: Annotated[str, Field(min_length=20, max_length=TAKEAWAY_MAX)] = Field(
        description="One sentence, at most 140 characters: the single most important point for "
        "JFrog, naming the affected JFrog product when there is one."
    )
    is_claim: bool = Field(
        description="True if the core statement is the vendor's own marketing claim."
    )
    new_entities: list[NewEntity] = Field(default_factory=list, max_length=5)


class DiscoveryNewEntity(NewEntity):
    """new_entities of a discovery item: may be an open-source project and carry its URL."""

    kind: Literal["company", "product", "open_source_project"]
    url: Annotated[str, Field(max_length=300)] | None = Field(
        default=None,
        description="Homepage or repository URL exactly as written in the text, else null.",
    )


class EnrichedDiscoveryBase(EnrichedSignalBase):
    """Discovery items (HN, GitHub search, CNCF landscape, D57): the item's own subject is the
    first new entity when it is untracked."""

    new_entities: list[DiscoveryNewEntity] = Field(default_factory=list, max_length=5)


class EnrichedPageDiffBase(EnrichedSignalBase):
    """Page diffs also get a specific headline and the price changes they show."""

    display_title: Annotated[str, Field(min_length=15, max_length=DISPLAY_TITLE_MAX)] = Field(
        description="Short, specific headline for the change (at most 80 characters)."
    )
    price_changes: list[ProposedPriceChange] = Field(default_factory=list, max_length=8)


def enrich_schema(
    entity_slugs: list[str],
    capability_slugs: list[str],
    *,
    page_diff: bool = False,
    discovery: bool = False,
) -> type[EnrichedSignalBase]:
    """EnrichedSignal (or EnrichedPageDiff / EnrichedDiscovery) with entity/capability slugs
    restricted to the lists."""
    if not entity_slugs or not capability_slugs:
        raise ValueError("enrich_schema needs at least one entity and one capability slug")
    entity_slug = Literal[tuple(sorted(entity_slugs))]
    capability_slug = Literal[tuple(sorted(capability_slugs))]
    entity_ref = create_model(
        "EntityRef",
        __base__=_Out,
        slug=(entity_slug, ...),
        role=(Literal["primary", "mention"], Field(description="primary = the news is about it")),
    )
    if page_diff:
        name, base = "EnrichedPageDiff", EnrichedPageDiffBase
    elif discovery:
        name, base = "EnrichedDiscovery", EnrichedDiscoveryBase
    else:
        name, base = "EnrichedSignal", EnrichedSignalBase
    return create_model(
        name,
        __base__=base,
        entities=(list[entity_ref], Field(default_factory=list, max_length=10)),
        capabilities=(list[capability_slug], Field(default_factory=list, max_length=6)),
    )


@dataclass(frozen=True)
class EnrichSchemas:
    """The run's enrich schemas: articles, page diffs and discovery items."""

    article: type[EnrichedSignalBase]
    page_diff: type[EnrichedSignalBase]
    discovery: type[EnrichedSignalBase] | None = None

    def pick(self, diff: bool, discovery: bool = False) -> type[EnrichedSignalBase]:
        if diff:
            return self.page_diff
        if discovery and self.discovery is not None:
            return self.discovery
        return self.article


def enrich_schemas(entity_slugs: list[str], capability_slugs: list[str]) -> EnrichSchemas:
    return EnrichSchemas(
        enrich_schema(entity_slugs, capability_slugs),
        enrich_schema(entity_slugs, capability_slugs, page_diff=True),
        enrich_schema(entity_slugs, capability_slugs, discovery=True),
    )


# ---------- verify ----------


class VerifyResult(_Out):
    verdict: Literal["supported", "partial", "unsupported"]
    notes: soft_text(400) = Field(description="1-2 sentences.")
    unsupported_claims: list[soft_text(200)] = Field(default_factory=list, max_length=5)
