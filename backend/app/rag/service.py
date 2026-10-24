"""One Ask turn end to end: conversation → standalone query → filters → retrieval → checked answer
→ stored turn with its cost (D65-D67).

The turn row is committed before any LLM call: the gateway writes llm_calls rows (with
ask_turn_id) in its own transaction, and they reference it. The turn's cost is the sum of those
rows; `cached` = every call was a cache hit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AskAnswerOut,
    AskFilters,
    AskMeaning,
    AskOut,
    AskPoint,
    AskSource,
)
from app.core.catalog import AskSettings, load_pipeline
from app.core.llm import llm_context
from app.db.enums import AskSource as ConversationSource
from app.db.models import AskConversation, AskTurn, LLMCall
from app.pipeline.context import CompleteFn, EmbedFn
from app.rag.answer import AnswerResult, Item, answer
from app.rag.documents import Doc
from app.rag.filters import Filters, infer_filters
from app.rag.retrieve import Retrieval, retrieve
from app.rag.rewrite import PastTurn, rewrite
from app.rag.schemas import plain

NO_DOCUMENTS_GAP = "FrogRadar has no stored intelligence that matches this question."
NO_DOCUMENTS_SUGGESTIONS = [
    "Check the spelling of the company or product name, or ask without a time frame.",
    "Add a source for this company in config/sources.yaml or config/competitors.yaml.",
    "Send a research mission to collect public information on this topic.",
]


@dataclass(frozen=True)
class LLMFns:
    complete: CompleteFn
    embed: EmbedFn


class AskInputError(ValueError):
    """Bad question or unknown conversation (→ 4xx)."""


def _history(session: Session, conversation_id: int) -> list[PastTurn]:
    turns = session.scalars(
        select(AskTurn)
        .where(AskTurn.conversation_id == conversation_id, AskTurn.error.is_(None))
        .order_by(AskTurn.position)
    ).all()
    return [
        PastTurn(t.question, tuple(p.get("lead", "") for p in (t.answer or {}).get("points", [])))
        for t in turns
    ]


def _number(result: AnswerResult, docs: list[Doc]) -> tuple[list[AskSource], dict[str, int]]:
    by_id = {d.id: d for d in docs}
    numbers: dict[str, int] = {}
    items: list[Item] = [*result.points, *([result.meaning] if result.meaning else [])]
    for it in items:
        for i in it.doc_ids:
            numbers.setdefault(i, len(numbers) + 1)
    sources = [
        AskSource(
            n=n,
            id=i,
            type=by_id[i].type,
            title=by_id[i].title,
            source=by_id[i].source,
            source_kind=by_id[i].source_kind,
            date=by_id[i].date,
            url=by_id[i].url,
            confidence=by_id[i].confidence,
            confidence_band=by_id[i].band,
        )
        for i, n in numbers.items()
    ]
    return sources, numbers


def _filters_out(f: Filters) -> AskFilters:
    return AskFilters(company=f.company_name, company_slug=f.company_slug, window=f.window)


def _turn_cost(session: Session, turn_id: int) -> tuple[Decimal | None, bool]:
    n, misses, total = session.execute(
        select(
            func.count(),
            func.count().filter(LLMCall.cache_hit.is_(False)),
            func.coalesce(func.sum(LLMCall.cost_usd), 0),
        ).where(LLMCall.ask_turn_id == turn_id)
    ).one()
    return (Decimal(total) if n else Decimal(0)), bool(n) and misses == 0


def ask(
    session: Session,
    question: str,
    *,
    conversation_id: int | None,
    llm: LLMFns,
    now: datetime,
    cfg: AskSettings | None = None,
    on_retrieval: Callable[[Retrieval], None] | None = None,
    source: ConversationSource = ConversationSource.UI,
) -> AskOut:
    cfg = cfg or load_pipeline().ask
    question = " ".join(question.split())
    if len(question) < 3:
        raise AskInputError("Ask a question of at least 3 characters.")
    question = question[: cfg.question_max_chars]

    if conversation_id is not None:
        conv = session.get(AskConversation, conversation_id)
        if conv is None:
            raise AskInputError(f"No conversation {conversation_id}")
        history = _history(session, conv.id)
    else:
        conv = AskConversation(title=question[:200], source=source)
        session.add(conv)
        session.flush()
        history = []
    position = (
        session.scalar(
            select(func.coalesce(func.max(AskTurn.position), 0)).where(
                AskTurn.conversation_id == conv.id
            )
        )
        or 0
    ) + 1
    turn = AskTurn(
        conversation_id=conv.id, position=position, question=question, standalone_question=question
    )
    session.add(turn)
    conv.updated_at = now
    session.commit()  # llm_calls rows written by the gateway reference this turn

    try:
        with llm_context(ask_turn_id=turn.id):
            standalone, rewrite_version = rewrite(
                question, history, llm.complete, max_turns=cfg.history_turns
            )
            filters = infer_filters(session, standalone, now)
            found = retrieve(session, standalone, filters, embed=llm.embed, cfg=cfg, now=now)
            if on_retrieval:
                on_retrieval(found)
            if found.hits:
                result = answer(
                    session,
                    standalone,
                    found.docs,
                    llm.complete,
                    now=now,
                    doc_max_chars=cfg.doc_max_chars,
                )
            else:
                result = AnswerResult(
                    not_enough_evidence=True,
                    gap=NO_DOCUMENTS_GAP,
                    suggestions=list(NO_DOCUMENTS_SUGGESTIONS),
                )
    except Exception as e:
        session.rollback()
        turn = session.get(AskTurn, turn.id)  # type: ignore[assignment]
        if turn is not None:
            turn.error = f"{type(e).__name__}: {str(e)[:500]}"
            session.commit()
        raise

    if rewrite_version:
        result.prompt_versions["rewrite"] = rewrite_version
    sources, numbers = _number(result, found.docs)
    out = AskAnswerOut(
        turn_id=turn.id,
        position=position,
        question=question,
        standalone_question=standalone,
        filters=_filters_out(filters),
        not_enough_evidence=result.not_enough_evidence,
        intro=result.intro,
        points=[
            AskPoint(lead=p.lead or "", text=p.text, citations=[numbers[i] for i in p.doc_ids])
            for p in result.points
        ],
        meaning_for_jfrog=(
            AskMeaning(
                text=result.meaning.text, citations=[numbers[i] for i in result.meaning.doc_ids]
            )
            if result.meaning
            else None
        ),
        gap=result.gap,
        suggestions=result.suggestions,
        suggested_followups=result.followups,
        sources=sources,
        retrieved=len(found.hits),
        created_at=turn.created_at,
    )
    cost, cached = _turn_cost(session, turn.id)
    out.cost_usd = float(cost) if cost is not None else None
    out.cached = cached

    turn.standalone_question = standalone
    turn.filters = filters.as_dict()
    turn.retrieved = [h.as_dict() for h in found.hits]
    turn.answer = out.model_dump(mode="json")
    turn.dropped = result.dropped
    turn.not_enough_evidence = result.not_enough_evidence
    turn.prompt_versions = result.prompt_versions
    turn.cost_usd = cost
    turn.cached = cached
    session.commit()
    return AskOut(conversation_id=conv.id, **out.model_dump())


def stored_answer(turn: AskTurn) -> AskAnswerOut | None:
    """A stored turn as shown (None for failed turns)."""
    if turn.error or not turn.answer:
        return None
    data: dict[str, Any] = dict(turn.answer)
    data["points"] = [{**p, "lead": plain(p.get("lead", ""))} for p in data.get("points", [])]
    return AskAnswerOut.model_validate(data)
