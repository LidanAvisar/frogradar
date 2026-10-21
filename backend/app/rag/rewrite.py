"""Follow-up questions → one standalone query (bulk model, ask_rewrite.v1), from the last turns."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.llm import load_prompt
from app.pipeline.context import CompleteFn
from app.pipeline.prompting import delimit
from app.rag.schemas import Rewrite

PROMPT = "ask_rewrite"
HISTORY_MAX_CHARS = 4000


@dataclass(frozen=True)
class PastTurn:
    question: str
    leads: tuple[str, ...]  # the answer's point leads ("not enough evidence" if none)


def render_history(turns: Sequence[PastTurn]) -> str:
    lines = []
    for i, t in enumerate(turns, start=1):
        answer = "; ".join(t.leads) if t.leads else "not enough evidence"
        lines.append(f"{i}. Q: {t.question}\n   A: {answer}")
    return "\n".join(lines)


def rewrite(
    question: str, history: Sequence[PastTurn], complete: CompleteFn, *, max_turns: int
) -> tuple[str, str | None]:
    """(standalone question, prompt version). No history → the question itself, no call."""
    turns = list(history)[-max_turns:] if max_turns else []
    if not turns:
        return question, None
    prompt = load_prompt(PROMPT)
    tag, body = delimit(
        f"EARLIER TURNS (oldest first):\n{render_history(turns)}\n\n"
        f"FOLLOW-UP QUESTION:\n{question}",
        HISTORY_MAX_CHARS,
    )
    messages = prompt.messages(tag=tag, text=body)
    out = complete("bulk", PROMPT, messages, Rewrite, prompt_version=prompt.id)
    return out.standalone_question, prompt.id
