"""`make llm-smoke`: one tiny call per tier plus one embedding, to confirm keys and routing work.

Bypasses the cache so every call really reaches the provider. Prints model, latency and cost per
call; exits 1 if any call fails. Never prints keys (errors are scrubbed).
"""

import logging
import sys
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.llm import TASKS, LLMError, get_gateway, load_prompt
from app.core.llm.errors import scrub
from app.db.models import LLMCall
from app.db.session import get_engine

OPERATION = "llm_smoke"


class Pong(BaseModel):
    answer: Literal["pong"]
    tier: str


def _last_call() -> LLMCall | None:
    with sessionmaker(bind=get_engine())() as s:
        return s.scalars(
            select(LLMCall).where(LLMCall.operation == OPERATION).order_by(LLMCall.id.desc())
        ).first()


def _row(task: str, model: str, status: str, call: LLMCall | None) -> str:
    if call is None:
        return f"{task:<10} {model:<34} {status:<6} {'-':>8} {'-':>11} {'-':>12}"
    cost = "unknown" if call.cost_usd is None else f"${call.cost_usd:.6f}"
    tokens = f"{call.tokens_in}/{call.tokens_out}"
    return f"{task:<10} {model:<34} {status:<6} {call.latency_ms:>6}ms {tokens:>11} {cost:>12}"


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    gw = get_gateway()
    if gw.settings.demo_mode:
        print("DEMO_MODE is on: providers are never called. Unset it to run the smoke test.")
        return 1

    prompt = load_prompt("smoke")
    routing = gw.routing
    header = ("task", "model", "status", "latency", "tokens", "cost")
    print("{:<10} {:<34} {:<6} {:>8} {:>11} {:>12}".format(*header))
    failures = 0
    for task in TASKS:
        model = getattr(routing.tasks, task).model
        try:
            gw.complete(
                task,
                OPERATION,
                prompt.messages(tier=task),
                Pong,
                prompt_version=prompt.id,
                use_cache=False,
            )
            print(_row(task, model, "ok", _last_call()))
        except LLMError as e:
            failures += 1
            print(_row(task, model, "FAIL", None))
            print(f"           {type(e).__name__}: {scrub(str(e), 300)}")

    model = routing.embedding.model
    try:
        result = gw.embed(["FrogRadar smoke test"], operation=OPERATION, use_cache=False)
        print(_row("embedding", model, "ok", _last_call()) + f"  dims={result.dimensions}")
    except LLMError as e:
        failures += 1
        print(_row("embedding", model, "FAIL", None))
        print(f"           {type(e).__name__}: {scrub(str(e), 300)}")

    print("all calls ok" if not failures else f"{failures} call(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
