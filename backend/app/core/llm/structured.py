"""Structured output: request JSON for a Pydantic schema, parse it, explain failures."""

import json
import logging
import re
from typing import Any

import litellm
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

MAX_VALIDATION_RETRIES = 2  # so at most 3 provider calls per complete()

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def supports_schema(model: str) -> bool:
    """True if LiteLLM can enforce a JSON schema for this model (native or via tool use)."""
    try:
        return bool(litellm.supports_response_schema(model=model))
    except Exception:  # noqa: BLE001 - unknown model: fall back to JSON-in-text
        return False


def response_format(model_cls: type[BaseModel]) -> dict[str, Any]:
    # strict=False: OpenAI strict mode rejects optional fields / defaults; we validate ourselves.
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_cls.__name__,
            "schema": model_cls.model_json_schema(),
            "strict": False,
        },
    }


def with_schema_instruction(
    messages: list[dict[str, Any]], model_cls: type[BaseModel]
) -> list[dict[str, Any]]:
    """For models without response_format support: describe the schema in the system prompt."""
    instruction = (
        "Respond with a single JSON object only (no prose, no code fences) that validates "
        f"against this JSON schema:\n{json.dumps(model_cls.model_json_schema())}"
    )
    if messages and messages[0].get("role") == "system":
        first = {**messages[0], "content": f"{messages[0]['content']}\n\n{instruction}"}
        return [first, *messages[1:]]
    return [{"role": "system", "content": instruction}, *messages]


def extract_json(text: str) -> str:
    """Strip code fences / surrounding prose and return the outermost JSON object."""
    text = text.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def parse[M: BaseModel](text: str, model_cls: type[M]) -> tuple[M | None, str | None]:
    """Return (object, None) on success or (None, error description) on failure."""
    try:
        return model_cls.model_validate_json(extract_json(text)), None
    except ValidationError as e:
        errors = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in e.errors()[:10]
        )
        return None, errors


def retry_messages(previous_output: str, error: str) -> list[dict[str, Any]]:
    return [
        {"role": "assistant", "content": previous_output},
        {
            "role": "user",
            "content": (
                f"Your previous output failed validation: {error}\n"
                "Return only a corrected JSON object that matches the schema."
            ),
        },
    ]
