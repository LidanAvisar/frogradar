"""Structured outputs of the mission agent's LLM calls (validated, retried on failure)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.enums import SignalCategory


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SubQuestion(_Out):
    question: Annotated[str, Field(min_length=5, max_length=300)]
    search_query: Annotated[str, Field(min_length=2, max_length=200)]
    why: Annotated[str, Field(max_length=300)] = ""


class MissionPlan(_Out):
    title: Annotated[str, Field(min_length=3, max_length=120)]
    sub_questions: Annotated[list[SubQuestion], Field(min_length=3, max_length=5)]


class ExtractedFinding(_Out):
    statement: Annotated[str, Field(min_length=10, max_length=400)]
    source: Annotated[int, Field(ge=1, le=50)]  # [S<n>] number of the cited source
    evidence_quote: Annotated[str, Field(min_length=10, max_length=500)]
    confidence: Literal["high", "medium", "low"]
    is_claim: bool = False
    impact: Annotated[int, Field(ge=1, le=5)] = 2
    category: SignalCategory = SignalCategory.OTHER
    why_jfrog_cares: Annotated[str, Field(max_length=400)] = ""


class Extraction(_Out):
    findings: Annotated[list[ExtractedFinding], Field(max_length=10)]
    # What the sources did not answer (becomes an open question in the report).
    not_found: Annotated[str, Field(max_length=400)] = ""


class FindingVerdict(_Out):
    key: str
    verdict: Literal["supported", "partial", "unsupported"]
    note: Annotated[str, Field(max_length=600)] = ""


class Verdicts(_Out):
    items: list[FindingVerdict]


class ReportPoint(_Out):
    text: Annotated[str, Field(min_length=5, max_length=400)]
    findings: Annotated[list[str], Field(max_length=6)]  # finding keys, e.g. "F3"


class MissionReport(_Out):
    summary: Annotated[list[ReportPoint], Field(max_length=6)]
    open_questions: Annotated[list[Annotated[str, Field(max_length=300)]], Field(max_length=6)]
