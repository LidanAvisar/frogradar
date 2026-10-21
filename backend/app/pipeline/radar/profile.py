"""Radar profile: what a candidate is, in five cited statements (bulk model), checked twice (D60).

Only candidates with score >= profile.min_score (status new / watching), the top_n per run, and
only when their mention set changed since the last profile (profile_signal_hash) or with
--force radar.

Input: up to max_signals of the candidate's mention signals (relevant + enriched first, then
newest): id, title, source, date and the first excerpt_chars of the collected text. The writer
and the verifier see exactly the same text. raw_text is analysis input only: it is never stored
in the profile or returned by the API (users see the statements and links to the sources).

Checks, same rules as the brief / battlecards (D53):
1. citations (code): every id a statement cites must be one of the signals given; one bad id
   drops the statement (reason invalid_citation, per id: unknown / not_in_input);
2. the verify model (a different provider, verify_profile prompt) judges each remaining
   statement against the signals it cites (JFrog's portfolio is trusted context for the "JFrog
   relation" statement); partial / unsupported → dropped with the note.
what_it_is dropped → profile_status rejected (nothing shown but the candidate's raw facts).
Dropped statements are kept in profile.dropped.
"""

import hashlib
import logging
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import RadarProfileSettings
from app.core.llm import load_prompt
from app.db.enums import EntityReviewStatus, ProfileStatus, Relationship, Segment
from app.db.models import Entity, RadarCandidate, Signal, Source
from app.pipeline.context import CompleteFn
from app.pipeline.prompting import delimit
from app.pipeline.synthesis.citations import Checked, Claim
from app.pipeline.synthesis.schemas import check_schema

logger = logging.getLogger(__name__)

PROMPT = "radar_profile"
VERIFY_PROMPT = "verify_profile"
KEYS = ("what_it_is", "segments", "jfrog_relation", "stage", "why_notable_now")
REQUIRED = "what_it_is"
BLOCK_MAX_CHARS = 40000

Ids = Annotated[list[int], Field(min_length=1, max_length=4)]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Statement(_Out):
    text: Annotated[str, Field(min_length=10, max_length=240)]
    signal_ids: Ids = Field(description="Ids of the signals this statement relies on.")


class SegmentsStatement(Statement):
    segments: Annotated[list[Segment], Field(min_length=1, max_length=3)]


class StageStatement(Statement):
    stage: Literal["open_source_project", "startup", "feature_of_large_company", "unknown"]


class RelationStatementBase(Statement):
    relation: Literal["threatens", "complements", "both", "none"]

    @model_validator(mode="after")
    def _products_match_relation(self) -> Self:
        products = getattr(self, "products", [])
        if self.relation == "none" and products:
            raise ValueError('relation "none" means no JFrog products: leave products empty')
        if self.relation != "none" and not products:
            raise ValueError("name the JFrog products it threatens or complements")
        return self


class RadarProfileBase(_Out):
    what_it_is: Statement = Field(description="One sentence: what it is.")
    segments: SegmentsStatement
    stage: StageStatement
    why_notable_now: Statement = Field(description="One sentence: why it is notable now.")


def profile_schema(jfrog_products: list[str]) -> type[RadarProfileBase]:
    relation = create_model(
        "RelationStatement",
        __base__=RelationStatementBase,
        products=(
            Annotated[list[Literal[tuple(sorted(jfrog_products))]], Field(max_length=4)],
            ...,
        ),  # type: ignore[valid-type]
    )
    return create_model("RadarProfile", __base__=RadarProfileBase, jfrog_relation=(relation, ...))


def jfrog_products(session: Session) -> dict[str, str]:
    """slug -> "Name: description" of JFrog's current (not discontinued) products."""
    rows = session.scalars(
        select(Entity).where(
            Entity.relationship == Relationship.SELF,
            Entity.parent_id.is_not(None),
            Entity.discontinued_on.is_(None),
            Entity.review_status == EntityReviewStatus.APPROVED,
        )
    ).all()
    return {e.slug: f"{e.name}: {e.description or ''}".strip() for e in rows}


def signal_hash(ids: list[int]) -> str:
    return hashlib.sha256(",".join(str(i) for i in sorted(ids)).encode()).hexdigest()


def input_signals(
    session: Session, cand: RadarCandidate, cfg: RadarProfileSettings
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Signal.id,
            Signal.title,
            Signal.raw_text,
            Signal.published_at,
            Signal.fetched_at,
            Signal.relevant,
            Signal.enriched_at,
            Source.name,
        )  # fmt: skip
        .outerjoin(Source, Source.id == Signal.source_id)
        .where(Signal.id.in_(cand.mention_signal_ids or [-1]))
    ).all()
    rows = sorted(
        rows,
        key=lambda r: (
            not (r.relevant and r.enriched_at is not None),
            -(r.published_at or r.fetched_at).timestamp(),
        ),
    )[: cfg.max_signals]
    return [
        {
            "id": r.id,
            "title": r.title,
            "source": r.name or "research finding",
            "date": (r.published_at or r.fetched_at).date().isoformat(),
            "excerpt": r.raw_text[: cfg.excerpt_chars],
        }
        for r in rows
    ]


def render_signals(items: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[#{s['id']}] {s['title']}\nSource: {s['source']} · {s['date']}\n{s['excerpt']}"
        for s in items
    )


def claims_of(profile: RadarProfileBase) -> list[Claim]:
    out = []
    for key in KEYS:
        st = getattr(profile, key)
        extra: dict[str, Any] = {}
        text = st.text
        if key == "segments":
            extra["segments"] = [str(s) for s in st.segments]
            text = f"Segments: {', '.join(extra['segments'])}. {st.text}"
        elif key == "stage":
            extra["stage"] = st.stage
            text = f"Stage: {st.stage}. {st.text}"
        elif key == "jfrog_relation":
            extra["relation"] = st.relation
            extra["products"] = list(st.products)
            text = (
                f"No overlap with JFrog products: {st.text}"
                if st.relation == "none"
                else f"{st.relation} JFrog {', '.join(st.products)}: {st.text}"
            )
        out.append(Claim(key, text, tuple(st.signal_ids), {"statement": st.text, **extra}))
    return out


def check_profile_citations(session: Session, claims: list[Claim], allowed: set[int]) -> Checked:
    out = Checked()
    bad = sorted({i for c in claims for i in c.signal_ids if i not in allowed})
    existing = set(session.scalars(select(Signal.id).where(Signal.id.in_(bad or [-1]))))
    for c in claims:
        wrong = {
            str(i): ("not_in_input" if i in existing else "unknown")
            for i in c.signal_ids
            if i not in allowed
        }
        if wrong:
            out.drop(c, "invalid_citation", invalid_ids=wrong)
        else:
            out.kept.append(c)
    return out


def verify_statements(
    claims: list[Claim],
    signals: dict[int, dict[str, Any]],
    candidate: str,
    portfolio: str,
    complete: CompleteFn,
) -> tuple[Checked, str | None]:
    out = Checked()
    if not claims:
        return out, None
    prompt = load_prompt(VERIFY_PROMPT)
    cited = sorted({i for c in claims for i in c.signal_ids})
    text = render_signals([signals[i] for i in cited])
    claims_text = "\n".join(
        f"{c.key}: {c.text} [cites {', '.join(f'#{i}' for i in c.signal_ids)}]" for c in claims
    )
    tag, body = delimit(f"{text}\n\nSTATEMENTS:\n{claims_text}", BLOCK_MAX_CHARS)
    messages = prompt.messages(tag=tag, text=body, candidate=candidate, jfrog_portfolio=portfolio)
    result = complete(
        "verify",
        VERIFY_PROMPT,
        messages,
        check_schema([c.key for c in claims]),
        prompt_version=prompt.id,
    )
    verdicts = {v.key: v for v in result.items}  # type: ignore[attr-defined]
    for c in claims:
        v = verdicts.get(c.key)
        if v is None:
            out.drop(c, "not_verified")
        elif v.verdict == "supported":
            out.kept.append(c)
        else:
            out.drop(c, f"verifier_{v.verdict}", verifier_note=v.note)
    return out, prompt.id


def build_profile(
    session: Session,
    cand: RadarCandidate,
    cfg: RadarProfileSettings,
    complete: CompleteFn,
    now: datetime,
    *,
    model: str | None = None,
) -> str:
    """Generate, check and store the profile. Returns the profile_status value."""
    products = jfrog_products(session)
    items = input_signals(session, cand, cfg)
    by_id = {s["id"]: s for s in items}
    portfolio = "\n".join(f"- {slug} | {text}" for slug, text in sorted(products.items()))
    prompt = load_prompt(PROMPT)
    tag, body = delimit(render_signals(items), BLOCK_MAX_CHARS)
    contexts = list(((cand.meta or {}).get("contexts") or {}).values())[:3]
    messages = prompt.messages(
        tag=tag,
        text=body,
        name=cand.name,
        kind=str(cand.kind),
        homepage=cand.homepage or "(none)",
        repo_url=cand.repo_url or "(none)",
        contexts="\n".join(f"- {c}" for c in contexts) or "(none)",
        jfrog_portfolio=portfolio,
        segments=", ".join(s.value for s in Segment),
        count=len(items),
    )
    schema = profile_schema(list(products))
    out = complete("bulk", PROMPT, messages, schema, prompt_version=prompt.id)

    claims = claims_of(out)
    cited = check_profile_citations(session, claims, set(by_id))
    verified, verify_version = verify_statements(
        cited.kept, by_id, f"{cand.name} ({cand.kind})", portfolio, complete
    )
    kept = {c.key: c.as_dict() for c in verified.kept}
    status = ProfileStatus.OK if REQUIRED in kept else ProfileStatus.REJECTED
    cand.profile = {
        "prompt_version": prompt.id,
        "verify_prompt_version": verify_version,
        "model": model,
        "generated_at": now.isoformat(),
        "input_signal_ids": sorted(by_id),
        "items": kept,
        "dropped": [*cited.dropped, *verified.dropped],
    }
    cand.profile_status = status
    cand.profile_signal_hash = signal_hash(cand.mention_signal_ids)
    return status.value
