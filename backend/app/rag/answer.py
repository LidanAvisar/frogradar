"""Answer from retrieved documents, then the S10 checks (D66).

1. ask.v1 (synthesis model) writes 2-6 cited points + an optional "what it means for JFrog", or
   says not_enough_evidence (then no check call is made).
2. Citation check in code: an item citing any id outside the retrieved set is dropped
   (invalid_citation) – one invented id is a sign of fabrication.
3. verify_ask.v1 (verify model, a different provider) judges each remaining item against the
   documents it cites; only "supported" is kept.
4. No point left → not_enough_evidence with an explaining gap. Dropped items are returned to the
   caller for storage only; the API never shows them.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import load_prompt
from app.pipeline.context import CompleteFn
from app.pipeline.prompting import Catalog, delimit
from app.pipeline.synthesis.check import jfrog_levels_text
from app.pipeline.synthesis.schemas import check_schema
from app.rag.documents import Doc
from app.rag.schemas import AskAnswer

PROMPT = "ask"
VERIFY_PROMPT = "verify_ask"
DOCS_MAX_CHARS = 30000
QUESTION_MAX_CHARS = 2000
NO_VERIFIED_POINT = "The documents found did not support a verified answer to this question."


@dataclass
class Item:
    key: str  # "point.1" ... or "meaning"
    lead: str | None
    text: str
    doc_ids: list[str]


@dataclass
class AnswerResult:
    not_enough_evidence: bool
    intro: str | None = None
    points: list[Item] = field(default_factory=list)
    meaning: Item | None = None
    gap: str | None = None
    suggestions: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    prompt_versions: dict[str, str] = field(default_factory=dict)


def documents_block(docs: list[Doc], max_chars_each: int) -> str:
    return "\n\n".join(d.render(max_chars_each) for d in docs)


def _drop(result: AnswerResult, item: Item, reason: str, **detail: Any) -> None:
    result.dropped.append(
        {"key": item.key, "lead": item.lead, "text": item.text, "doc_ids": item.doc_ids,
         "reason": reason, **detail}
    )  # fmt: skip


def answer(
    session: Session,
    question: str,
    docs: list[Doc],
    complete: CompleteFn,
    *,
    now: datetime,
    doc_max_chars: int,
) -> AnswerResult:
    portfolio = Catalog.load(session).jfrog_portfolio
    prompt = load_prompt(PROMPT)
    qtag, qtext = delimit(question, QUESTION_MAX_CHARS)
    tag, dtext = delimit(documents_block(docs, doc_max_chars), DOCS_MAX_CHARS)
    messages = prompt.messages(
        today=now.date().isoformat(),
        jfrog_portfolio=portfolio,
        qtag=qtag,
        question=qtext,
        tag=tag,
        documents=dtext,
    )
    out = complete("synthesis", PROMPT, messages, AskAnswer, prompt_version=prompt.id)
    result = AnswerResult(
        not_enough_evidence=out.not_enough_evidence,
        intro=out.intro,
        gap=out.gap,
        suggestions=list(out.suggestions),
        followups=list(out.followups),
        prompt_versions={"ask": prompt.id},
    )
    if out.not_enough_evidence:
        return result

    items = [
        Item(f"point.{i}", p.lead, p.text, list(dict.fromkeys(p.doc_ids)))
        for i, p in enumerate(out.points, start=1)
    ]
    if out.meaning_for_jfrog:
        m = out.meaning_for_jfrog
        items.append(Item("meaning", None, m.text, list(dict.fromkeys(m.doc_ids))))

    # 1. Citation check (code).
    allowed = {d.id for d in docs}
    cited_ok = []
    for it in items:
        bad = [i for i in it.doc_ids if i not in allowed]
        if bad:
            _drop(result, it, "invalid_citation", invalid_ids=bad)
        else:
            cited_ok.append(it)

    # 2. Verify model, against each item's cited documents only.
    kept = verify(session, question, cited_ok, docs, complete, portfolio, result)

    result.points = [it for it in kept if it.key != "meaning"]
    result.meaning = next((it for it in kept if it.key == "meaning"), None)
    if not result.points:
        result.not_enough_evidence = True
        result.meaning = None
        result.intro = None
        result.gap = NO_VERIFIED_POINT
    return result


def verify(
    session: Session,
    question: str,
    items: list[Item],
    docs: list[Doc],
    complete: CompleteFn,
    portfolio: str,
    result: AnswerResult,
) -> list[Item]:
    if not items:
        return []
    by_id = {d.id: d for d in docs}
    cited = [by_id[i] for i in dict.fromkeys(i for it in items for i in it.doc_ids)]
    claims = "\n".join(
        f"{it.key}: {(it.lead + ' – ') if it.lead else ''}{it.text} [cites {', '.join(it.doc_ids)}]"
        for it in items
    )
    prompt = load_prompt(VERIFY_PROMPT)
    tag, body = delimit(
        f"QUESTION (what the answer responds to):\n{question[:QUESTION_MAX_CHARS]}\n\n"
        f"DOCUMENTS:\n{documents_block(cited, 4000)}\n\nCLAIMS:\n{claims}",
        DOCS_MAX_CHARS + 6000,
    )
    messages = prompt.messages(
        tag=tag,
        text=body,
        jfrog_portfolio=portfolio,
        jfrog_levels=jfrog_levels_text(),
    )
    schema = check_schema([it.key for it in items])
    out = complete("verify", VERIFY_PROMPT, messages, schema, prompt_version=prompt.id)
    result.prompt_versions["verify"] = prompt.id
    verdicts = {v.key: v for v in out.items}  # type: ignore[attr-defined]
    kept = []
    for it in items:
        v = verdicts.get(it.key)
        if v is None:
            _drop(result, it, "not_verified")
        elif v.verdict == "supported":
            kept.append(it)
        else:
            _drop(result, it, f"verifier_{v.verdict}", verifier_note=v.note)
    return kept
