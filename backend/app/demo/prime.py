"""`make demo-prime` (PAID, live keys): ask the curated demo questions against the dev DB with the
clock pinned to the coming snapshot's as-of time, so their answers land in the LLM cache that
`make demo-snapshot` exports. Already-cached questions cost $0.

    python -m app.demo.prime            # prints the estimate, asks for nothing
    python -m app.demo.prime --yes      # runs (cost ≈ cost_estimates_usd.ask per uncached question)

Run it right before `make demo-snapshot`: the cache key contains the retrieved documents, so any
data change between priming and export (a new pipeline run, a rejected signal) can miss again.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.demo.prime")
    p.add_argument("--yes", action="store_true", help="make the paid calls")
    p.add_argument("--as-of", help="ISO time (default: the latest finished pipeline run)")
    args = p.parse_args(argv)

    from app.core.config import get_settings
    from app.db.session import get_engine

    as_of = args.as_of
    if not as_of:
        with get_engine().connect() as c:
            latest = c.execute(text("SELECT max(finished_at) FROM pipeline_runs")).scalar()
        if latest is None:
            print("no finished pipeline run: pass --as-of", file=sys.stderr)
            return 1
        as_of = latest.isoformat()
    os.environ["PIN_NOW"] = as_of  # same "today" in the prompt as the demo will have
    get_settings.cache_clear()

    from app.core import clock
    from app.core.catalog import load_llm_routing
    from app.core.llm import complete, embed
    from app.demo.questions import demo_questions
    from app.rag.service import LLMFns, ask

    questions = demo_questions()
    per_q = load_llm_routing().cost_estimates_usd.ask
    print(
        f"demo-prime: {len(questions)} questions, now pinned to {clock.now().isoformat()}; "
        f"about ${per_q:.2f} per uncached question (max ${per_q * len(questions):.2f})"
    )
    if not args.yes:
        print("estimate only: re-run with --yes (make demo-prime ARGS=--yes) to make the calls")
        return 0
    llm = LLMFns(complete=complete, embed=embed)
    total = 0.0
    failed = 0
    from sqlalchemy.orm import Session

    with Session(get_engine()) as s:
        for q in questions:
            try:
                out = ask(s, q, conversation_id=None, llm=llm, now=clock.now())
            except Exception as e:  # noqa: BLE001 - report and continue with the next question
                failed += 1
                print(f"  FAIL {q[:70]}: {type(e).__name__}: {e}")
                continue
            cost = out.cost_usd or 0.0
            total += cost
            tag = "cached" if out.cached else f"${cost:.4f}"
            print(f"  ok   {q[:70]} ({tag}, {len(out.points)} points)")
    print(f"demo-prime: total ${total:.4f}" + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
