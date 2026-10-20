"""Cost of a call: LiteLLM's price table first, then `pricing` in llm_routing.yaml, else unknown."""

import logging
from decimal import Decimal
from typing import Any

import litellm

from app.core.catalog import ModelPricing

logger = logging.getLogger(__name__)

_PLACES = Decimal("1e-10")  # matches llm_calls.cost_usd NUMERIC(14, 10)


def compute_cost(
    *,
    model: str,
    raw_response: Any,
    tokens_in: int,
    tokens_out: int,
    pricing: dict[str, ModelPricing],
    call_type: str = "completion",
) -> Decimal | None:
    """USD cost, or None (with a warning) if neither LiteLLM nor the YAML knows the model.

    A 0 from LiteLLM for a call that used tokens is treated as unknown, never as free.
    """
    try:
        cost = litellm.completion_cost(completion_response=raw_response, call_type=call_type)
    except Exception:  # noqa: BLE001 - LiteLLM raises for models missing from its price table
        cost = None
    if cost and cost > 0:
        return Decimal(str(cost)).quantize(_PLACES)

    price = pricing.get(model)
    if price is not None:
        usd = (tokens_in * price.input_per_mtok_usd + tokens_out * price.output_per_mtok_usd) / 1e6
        return Decimal(str(usd)).quantize(_PLACES)

    if tokens_in or tokens_out:
        logger.warning(
            "cost unknown for model %s (not in LiteLLM's price table or llm_routing.yaml "
            "`pricing`); recording cost_usd=NULL",
            model,
        )
        return None
    return Decimal(0)
