"""`make demo-check`: restore the committed snapshot into a throwaway DB and replay the demo.

Checks, in demo mode with no provider keys: Brief stats and "since yesterday" are not empty (the
clock is pinned to the snapshot's as-of time), every curated Ask question is answered from the cache
with zero provider calls, the main pages and MCP tools answer, new missions are refused, and the
stripped columns are empty. Exit code 1 on any failure.

    python -m app.demo.check            # creates, uses and drops frogradar_demo_check_<random>
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY")


def _orchestrate() -> int:
    from app.core.config import get_settings

    base = make_url(get_settings().database_url)
    name = f"frogradar_demo_check_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    env = {k: v for k, v in os.environ.items() if k not in PROVIDER_KEYS}
    env.update(DATABASE_URL=url, DEMO_MODE="true", MISSION_WORKER="false", MCP_TOKEN="")
    try:
        for cmd in (
            ["alembic", "upgrade", "head"],
            [sys.executable, "-m", "app.demo.snapshot", "restore"],
            [sys.executable, "-m", "app.demo.check", "--run"],
        ):
            r = subprocess.run(cmd, env=env, check=False)
            if r.returncode != 0:
                print(f"demo-check: {' '.join(cmd)} failed", file=sys.stderr)
                return 1
        return 0
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _run_checks() -> int:  # noqa: C901 - a flat list of checks
    from fastapi.testclient import TestClient
    from mcp import Client

    from app.core import clock
    from app.db.session import get_engine
    from app.demo.questions import demo_questions
    from app.main import create_app
    from app.mcp.server import build_server

    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    print(f"demo-check: now pinned to {clock.now().isoformat()}")
    c = TestClient(create_app())
    st = c.get("/api/status").json()
    check(st["mode"] == "demo" and bool(st["demo_questions"]), "status: demo mode + questions")

    brief = c.get("/api/brief").json()
    stats = brief["stats"]
    check(stats["new_signals"] > 0, f"brief: {stats['new_signals']} new signals in 24h")
    check(brief["top_story"] is not None, "brief: top story")
    check(len(brief["since_yesterday"]) + len(brief["developments"]) > 0, "brief: developments")

    for path in ("/api/signals", "/api/radar", "/api/battlecards", "/api/missions",
                 "/api/entities/sonatype/battlecard", "/api/review", "/api/nav",
                 "/api/feedback/scorecard"):  # fmt: skip
        r = c.get(path)
        check(r.status_code == 200, f"GET {path} → {r.status_code}")
    missions = c.get("/api/missions").json()["items"]
    if missions:
        r = c.get(f"/api/missions/{missions[0]['id']}")
        check(r.status_code == 200, f"mission {missions[0]['id']} detail")
    r = c.post("/api/missions", json={"brief": "Check the demo refuses new missions."})
    check(r.status_code == 409, f"new mission refused ({r.status_code})")

    engine = get_engine()
    with engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM llm_calls WHERE NOT cache_hit")).scalar()
    for q in demo_questions():
        r = c.post("/api/ask", json={"question": q})
        ok = r.status_code == 200 and r.json().get("cached") is True
        check(ok, f"ask (cached): {q[:70]} → {r.status_code}")
    r = c.post("/api/ask", json={"question": "What's the weather in Tel Aviv?"})
    check(r.status_code == 409, "ask: other questions get the demo message")
    with engine.connect() as conn:
        after = conn.execute(text("SELECT count(*) FROM llm_calls WHERE NOT cache_hit")).scalar()
        check(after == before, f"zero provider calls / cache misses ({after - before})")
        empty = conn.execute(
            text(
                "SELECT (SELECT count(*) FROM signals WHERE raw_text <> '') + "
                "(SELECT count(*) FROM page_snapshots WHERE text <> '') + "
                "(SELECT count(*) FROM mission_sources WHERE excerpt <> '')"
            )
        ).scalar()
        check(empty == 0, "raw_text / page text / excerpts stripped")

    async def mcp_calls() -> dict:
        async with Client(build_server()) as m:
            out = {}
            calls = [("whats_new", {}), ("get_battlecard", {"company": "sonatype"}),
                     ("radar", {}), ("ask", {"question": demo_questions()[0]})]  # fmt: skip
            for name, args in calls:
                res = await m.call_tool(name, args)
                out[name] = (res.is_error, res.structured_content or {})
            return out

    for name, (is_error, content) in asyncio.run(mcp_calls()).items():
        detail = f" ({len(content.get('items', []))} items)" if "items" in content else ""
        check(not is_error, f"MCP {name}{detail}")

    print("demo-check: " + ("OK" if not failures else f"{len(failures)} failed"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_checks() if "--run" in sys.argv else _orchestrate())
