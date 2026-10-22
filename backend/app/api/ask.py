"""Ask your intelligence (S13): questions answered from stored intelligence only.

    POST /api/ask                          question (+ conversation_id) → checked, cited answer
    GET  /api/ask/conversations            recent conversations
    GET  /api/ask/conversations/{id}       one conversation's turns as shown
    POST /api/ask/turns/{id}/feedback      Helpful / Something's wrong → feedback row

Answers cite documents (signals, battlecard items, radar profiles); dropped points are stored with
the turn and never returned; raw_text is never read (app.rag.documents).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, true

from app.api.deps import DbSession, Now
from app.api.schemas import (
    AskConversationDetail,
    AskConversationOut,
    AskFeedbackIn,
    AskFeedbackOut,
    AskIn,
    AskOut,
)
from app.core.config import get_settings
from app.core.llm import BudgetExceeded, DemoCacheMiss, LLMError, complete, embed
from app.core.status import get_status
from app.db.enums import FeedbackAction, FeedbackTarget, ReasonCode
from app.db.models import AskConversation, AskTurn, Feedback
from app.demo.questions import DEMO_ASK_MESSAGE, demo_questions, match_demo_question
from app.rag.service import AskInputError, LLMFns, ask, stored_answer

router = APIRouter(prefix="/api/ask", tags=["ask"])

ANALYST = "anonymous"  # no auth yet (README "Known limitations")


def get_llm() -> LLMFns:
    """The gateway's complete / embed (tests override this dependency with fakes)."""
    return LLMFns(complete=complete, embed=embed)


Llm = Annotated[LLMFns, Depends(get_llm)]


def require_ask_keys() -> None:
    """Live mode: answer 503 with the reason when the keys Ask needs are missing (never crash)."""
    if get_settings().demo_mode:
        return  # demo mode answers the curated questions from the cache
    feature = get_status().feature("ask")
    if feature.state == "off":
        raise HTTPException(503, f"Free-form Ask is off: {feature.reason}.")


@router.post(
    "",
    response_model=AskOut,
    responses={404: {}, 409: {}, 422: {}, 429: {}, 502: {}, 503: {}},
    dependencies=[Depends(require_ask_keys)],
)
def post_ask(body: AskIn, session: DbSession, now: Now, llm: Llm) -> AskOut:
    question, conversation_id = body.question, body.conversation_id
    if get_settings().demo_mode:
        # Only the curated questions have cached answers; ask them as a new conversation so the
        # prompt (no history) matches the cached one. Nothing is written for other questions.
        curated = match_demo_question(question)
        if curated is None:
            raise HTTPException(
                409, {"demo": True, "message": DEMO_ASK_MESSAGE, "suggestions": demo_questions()}
            )
        question, conversation_id = curated, None
    try:
        return ask(session, question, conversation_id=conversation_id, llm=llm, now=now)
    except AskInputError as e:
        code = 404 if body.conversation_id is not None and "conversation" in str(e) else 422
        raise HTTPException(code, str(e)) from None
    except BudgetExceeded as e:
        raise HTTPException(429, f"LLM budget reached, try again tomorrow: {e}") from None
    except DemoCacheMiss:
        raise HTTPException(
            503, {"demo": True, "message": DEMO_ASK_MESSAGE, "suggestions": demo_questions()}
        ) from None
    except LLMError as e:
        raise HTTPException(502, f"The language model failed: {type(e).__name__}") from None


@router.get("/conversations", response_model=list[AskConversationOut])
def list_conversations(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    source: Annotated[
        Literal["ui", "eval", "all"],
        Query(description="ui = asked by analysts (default); eval = created by make eval-ask"),
    ] = "ui",
) -> list[AskConversationOut]:
    counts = (
        select(AskTurn.conversation_id, func.count().label("n"))
        .where(AskTurn.error.is_(None))
        .group_by(AskTurn.conversation_id)
        .subquery()
    )
    rows = session.execute(
        select(AskConversation, counts.c.n)
        .join(counts, counts.c.conversation_id == AskConversation.id)
        .where(AskConversation.source == source if source != "all" else true())
        .order_by(AskConversation.updated_at.desc(), AskConversation.id.desc())
        .limit(limit * 5)
    ).all()
    # The same question asked again (a repeat, or a cached demo question) is listed once: latest.
    seen: set[str] = set()
    out: list[AskConversationOut] = []
    for c, n in rows:
        key = " ".join(c.title.lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(AskConversationOut(id=c.id, title=c.title, turns=n, updated_at=c.updated_at))
    return out[:limit]


@router.get("/conversations/{conversation_id}", response_model=AskConversationDetail,
            responses={404: {}})  # fmt: skip
def get_conversation(conversation_id: int, session: DbSession) -> AskConversationDetail:
    conv = session.get(AskConversation, conversation_id)
    if conv is None:
        raise HTTPException(404, "No such conversation")
    turns = session.scalars(
        select(AskTurn).where(AskTurn.conversation_id == conv.id).order_by(AskTurn.position)
    ).all()
    shown = [a for a in (stored_answer(t) for t in turns) if a is not None]
    return AskConversationDetail(id=conv.id, title=conv.title, turns=shown)


@router.post("/turns/{turn_id}/feedback", response_model=AskFeedbackOut, responses={404: {}})
def feedback(turn_id: int, body: AskFeedbackIn, session: DbSession) -> AskFeedbackOut:
    turn = session.get(AskTurn, turn_id)
    if turn is None or turn.error:
        raise HTTPException(404, "No such answer")
    session.add(
        Feedback(
            target_type=FeedbackTarget.ASK_TURN,
            target_id=turn.id,
            action=FeedbackAction.RATE,
            rating=5 if body.helpful else 1,
            reason_code=ReasonCode(body.reason) if body.reason and not body.helpful else None,
            comment=body.comment,
            analyst=ANALYST,
        )
    )
    session.commit()
    return AskFeedbackOut(turn_id=turn.id, helpful=body.helpful)
