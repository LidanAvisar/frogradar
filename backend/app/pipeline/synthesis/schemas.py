"""Output schemas of the synthesis calls (brief, battlecard) and of their verification."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from app.pipeline.schemas import soft_text

HEADLINE_MAX = 90
CitedIds = Annotated[list[int], Field(min_length=1, max_length=5)]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- daily brief ----------


class CitedSentence(_Out):
    text: Annotated[str, Field(min_length=10, max_length=300)]
    signal_ids: CitedIds = Field(description="Ids of the signals this sentence relies on.")


class Headline(CitedSentence):
    text: Annotated[str, Field(min_length=10, max_length=HEADLINE_MAX)]


class DailyBrief(_Out):
    headline: Headline
    overview: Annotated[list[CitedSentence], Field(min_length=2, max_length=3)]


# ---------- battlecard ----------

Level = Literal["full", "partial", "planned", "none"]


class CitedItem(_Out):
    text: Annotated[str, Field(min_length=10, max_length=240)]
    signal_ids: Annotated[list[int], Field(min_length=1, max_length=4)]


class CapabilityClaimBase(_Out):
    level: Level
    evidence_signal_id: int
    note: soft_text(160) = Field(description="What the evidence signal shows, one short sentence.")


class BattlecardBase(_Out):
    positioning: CitedItem
    strengths: Annotated[list[CitedItem], Field(max_length=5)] = []
    weaknesses: Annotated[list[CitedItem], Field(max_length=5)] = []
    opportunities_for_jfrog: Annotated[list[CitedItem], Field(max_length=5)] = []
    blind_spots_for_jfrog: Annotated[list[CitedItem], Field(max_length=5)] = []


SECTIONS = (
    "strengths",
    "weaknesses",
    "opportunities_for_jfrog",
    "blind_spots_for_jfrog",
)


def battlecard_schema(capability_slugs: list[str]) -> type[BattlecardBase]:
    """Battlecard with capability slugs restricted to capabilities.yaml (an invented slug fails
    validation and costs a retry instead of reaching the matrix)."""
    if not capability_slugs:
        raise ValueError("battlecard_schema needs capability slugs")
    claim = create_model(
        "CapabilityClaim",
        __base__=CapabilityClaimBase,
        capability=(Literal[tuple(sorted(capability_slugs))], ...),
    )
    return create_model(
        "Battlecard",
        __base__=BattlecardBase,
        capabilities=(list[claim], Field(default_factory=list, max_length=20)),
    )


# ---------- verification of synthesized claims ----------


class ClaimVerdictBase(_Out):
    verdict: Literal["supported", "partial", "unsupported"]
    note: soft_text(240) = Field(description="One sentence: why.")


class SynthesisCheckBase(_Out):
    items: list[ClaimVerdictBase]


def check_schema(keys: list[str]) -> type[SynthesisCheckBase]:
    """One verdict per claim key, every key exactly once (else a validation retry)."""
    if not keys:
        raise ValueError("check_schema needs at least one key")
    verdict = create_model(
        "ClaimVerdict", __base__=ClaimVerdictBase, key=(Literal[tuple(keys)], ...)
    )

    def _all_keys(self: SynthesisCheckBase) -> SynthesisCheckBase:
        got = [i.key for i in self.items]  # type: ignore[attr-defined]
        missing = sorted(set(keys) - set(got))
        dupes = sorted({k for k in got if got.count(k) > 1})
        if missing or dupes:
            raise ValueError(f"answer every key exactly once; missing {missing}, repeated {dupes}")
        return self

    model = create_model(
        "SynthesisCheck",
        __base__=SynthesisCheckBase,
        items=(list[verdict], Field(min_length=len(keys), max_length=len(keys))),
        __validators__={"all_keys": model_validator(mode="after")(_all_keys)},
    )
    return model
