"""Structured outputs of the Ask prompts (validated by the gateway, retried on failure).

Length limits are soft (D38): over-long text is cut with "…" instead of failing the call.
"""

from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, Field, model_validator


def _cut(limit: int):  # noqa: ANN202
    def cut(v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return v if len(v) <= limit else v[: limit - 1].rstrip() + "…"
        return v

    return BeforeValidator(cut)


DocIds = Annotated[list[str], Field(min_length=1, max_length=4)]


class AnswerPoint(BaseModel):
    lead: Annotated[str, _cut(80), Field(min_length=1)]
    text: Annotated[str, _cut(300), Field(min_length=1)]
    doc_ids: DocIds


class Meaning(BaseModel):
    text: Annotated[str, _cut(400), Field(min_length=1)]
    doc_ids: DocIds


class AskAnswer(BaseModel):
    not_enough_evidence: bool
    intro: Annotated[str | None, _cut(160)] = None
    points: Annotated[list[AnswerPoint], Field(max_length=6)] = Field(default_factory=list)
    meaning_for_jfrog: Meaning | None = None
    gap: Annotated[str | None, _cut(300)] = None
    suggestions: Annotated[list[Annotated[str, _cut(200)]], Field(max_length=5)] = Field(
        default_factory=list
    )
    followups: list[Annotated[str, _cut(100)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.not_enough_evidence:
            self.points, self.meaning_for_jfrog = [], None
        elif not self.points:
            raise ValueError("give at least one point, or set not_enough_evidence to true")
        self.followups = [f for f in self.followups if f][:3]
        self.suggestions = self.suggestions[:3]
        return self


class Rewrite(BaseModel):
    standalone_question: Annotated[str, _cut(300), Field(min_length=1)]
