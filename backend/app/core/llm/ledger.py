"""llm_calls rows (cost/latency ledger) and the budget checks that read them."""

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.llm.errors import BudgetExceeded, MissionBudgetExceeded
from app.db.models import LLMCall, Mission

logger = logging.getLogger(__name__)


@dataclass
class CallRecord:
    task: str
    operation: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int | None = None
    cost_usd: Decimal | None = Decimal(0)
    latency_ms: int = 0
    cache_hit: bool = False
    cache_key: str | None = None
    prompt_version: str | None = None
    attempt: int = 1
    success: bool = True
    error: str | None = None
    run_id: int | None = None
    mission_id: int | None = None
    ask_turn_id: int | None = None


def add(session: Session, record: CallRecord) -> None:
    session.add(LLMCall(**asdict(record)))


def spend_today(session: Session) -> Decimal:
    """Total cost of today's calls (UTC day). Unknown (NULL) costs count as 0 here."""
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(LLMCall.created_at >= day_start)
    )
    return Decimal(total)


def mission_spend(session: Session, mission_id: int) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(LLMCall.mission_id == mission_id)
    )
    return Decimal(total)


def check_budgets(session: Session, *, daily_budget_usd: float, mission_id: int | None) -> None:
    """Raise if today's spend or the current mission's spend has reached its budget.

    Checked before each provider call, so the call that crosses the line still completes.
    """
    spent = spend_today(session)
    if spent >= Decimal(str(daily_budget_usd)):
        raise BudgetExceeded(
            f"daily LLM budget reached: spent ${spent:.4f} of ${daily_budget_usd:.2f} today (UTC)"
        )
    if mission_id is None:
        return
    budget = session.scalar(select(Mission.budget_usd).where(Mission.id == mission_id))
    if budget is None:
        logger.warning("mission %s not found; skipping its budget check", mission_id)
        return
    spent = mission_spend(session, mission_id)
    if spent >= budget:
        raise MissionBudgetExceeded(
            f"mission {mission_id} budget reached: spent ${spent:.4f} of ${budget:.2f}"
        )
