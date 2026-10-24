"""System status: keys present (booleans only), features, today's LLM spend vs budget.

GET /api/status
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DbSession
from app.core.catalog import load_llm_routing
from app.core.config import get_settings
from app.core.llm.ledger import spend_today
from app.core.status import get_status

router = APIRouter(prefix="/api", tags=["status"])


class ModelKeyOut(BaseModel):
    tier: str
    model: str
    provider: str
    key_present: bool
    env_vars: list[str] = Field(description="Env var names LiteLLM needs (names only, no values)")


class FeatureOut(BaseModel):
    name: str
    label: str
    state: Literal["enabled", "degraded", "off"]
    reason: str = ""


class CostEstimatesOut(BaseModel):
    ask: float
    mission: float
    daily_run: float
    mission_report: float = Field(description="A mission's report (the plan-approval estimate)")
    mission_per_question: float = Field(description="Extract + verify per mission sub-question")


class StatusOut(BaseModel):
    models: list[ModelKeyOut]
    extra_keys: dict[str, bool]
    features: list[FeatureOut]
    same_family_verify: bool
    warnings: list[str]
    spend_today_usd: float
    daily_budget_usd: float
    cost_estimates_usd: CostEstimatesOut


@router.get("/status", response_model=StatusOut)
def get_system_status(session: DbSession) -> StatusOut:
    st = get_status()
    settings = get_settings()
    return StatusOut(
        **st.as_dict(),
        spend_today_usd=float(spend_today(session)),
        daily_budget_usd=settings.llm_daily_budget_usd,
        cost_estimates_usd=CostEstimatesOut(**load_llm_routing().cost_estimates_usd.model_dump()),
    )
