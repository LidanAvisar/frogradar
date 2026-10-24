"""How far a mission got: the step limit an approved plan needs, and which sub-questions ran (D84).

Every sub-question runs the same counted phases (search → read → extract → verify), so the step
limit follows the approved plan: STEPS_PER_QUESTION × sub-questions, capped by max_max_steps.
A sub-question counts as researched once its verify step ran (step summaries start with "Q<n>:").
A finished mission with a sub-question that never ran is partial, whatever stopped it.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.catalog import MissionSettings
from app.db.enums import MissionStatus, MissionStepKind
from app.db.models import MissionStep

PHASES = ("search", "read", "extract", "verify")
STEPS_PER_QUESTION = len(PHASES)
# Before approval the plan isn't known: the plan schema allows up to 5 sub-questions.
PROVISIONAL_QUESTIONS = 5

UNFINISHED = (
    MissionStatus.QUEUED,
    MissionStatus.PLANNING,
    MissionStatus.AWAITING_APPROVAL,
    MissionStatus.RUNNING,
)


def steps_for(questions: int, cfg: MissionSettings) -> int:
    """The step limit a plan with `questions` sub-questions needs (at least 1, at most the cap)."""
    return max(1, min(STEPS_PER_QUESTION * questions, cfg.max_max_steps))


@dataclass
class Progress:
    total: int
    researched: list[int] = field(default_factory=list)  # 1-based sub-question numbers
    skipped: list[dict[str, Any]] = field(default_factory=list)  # [{"n", "question"}]
    partial: bool = False


def progress(status: MissionStatus, sub_questions: Sequence[dict[str, Any]],
             steps: Iterable[MissionStep]) -> Progress:  # fmt: skip
    """Which sub-questions ran. Skipped and partial are only reported once the mission ended."""
    verified = {
        int(st.summary[1 : st.summary.index(":")])
        for st in steps
        if st.kind == MissionStepKind.VERIFY
        and st.summary.startswith("Q")
        and ":" in st.summary
        and st.summary[1 : st.summary.index(":")].isdigit()
    }
    total = len(sub_questions)
    researched = [n for n in range(1, total + 1) if n in verified]
    if status in UNFINISHED:
        return Progress(total, researched)
    skipped = [
        {"n": n, "question": str(q.get("question") or "")}
        for n, q in enumerate(sub_questions, start=1)
        if n not in verified
    ]
    return Progress(total, researched, skipped, partial=bool(skipped))
