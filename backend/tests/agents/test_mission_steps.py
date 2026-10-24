"""The step limit follows the approved plan, and a cut-short mission says so (D84)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.agents.mission.runner import MissionRunner, Tools
from app.agents.mission.service import approve_plan
from app.core.catalog import MissionSettings
from app.core.llm import MissionBudgetExceeded
from app.db.enums import MissionStatus, MissionStepKind
from app.db.models import Mission
from tests.agents.conftest import ScriptedLLM, approve, default_extract, make_mission


def _plan(n: int):  # noqa: ANN202
    def plan(call: Any) -> dict[str, Any]:
        qs = [{"question": f"Sub-question number {i}?", "search_query": f"query {i}"}
              for i in range(1, n + 1)]  # fmt: skip
        qs[-1]["question"] = "How does this compare with JFrog's own offering?"
        return {"title": "A mission", "sub_questions": qs}

    return plan


def _research(s: Session, cfg: MissionSettings, tools: Tools, m: Mission) -> Mission:
    MissionRunner(s, m.id, tools, cfg).research()
    s.expire_all()
    return s.get(Mission, m.id)


def _planned(s: Session, cfg: MissionSettings, tools: Tools) -> Mission:
    m = make_mission(s, cfg)
    MissionRunner(s, m.id, tools, cfg).plan()
    s.refresh(m)
    return m


@pytest.mark.parametrize("n", [4, 5])
def test_a_plan_with_more_than_three_sub_questions_runs_every_one(s, cfg, tools, llm, n):
    llm.handlers["mission_plan"] = _plan(n)
    m = _planned(s, cfg, tools)
    approve(s, m)
    assert m.max_steps == 4 * n

    m = _research(s, cfg, tools, m)
    verified = [st for st in m.steps if st.kind == MissionStepKind.VERIFY]
    assert len(verified) == n  # the last (JFrog comparison) sub-question ran too
    assert m.status == MissionStatus.COMPLETED and "stopped_reason" in m.report
    assert m.report["stopped_reason"] is None
    assert m.report["partial"] is False and m.report["skipped"] == []


def test_an_edited_plan_sets_the_step_limit_and_the_cap_still_applies(s, tools, llm):
    llm.handlers["mission_plan"] = _plan(3)
    cfg = MissionSettings(kb_results=0, web_results=2, pages_per_question=2, max_max_steps=20)
    m = _planned(s, cfg, tools)
    six = [{"question": f"Edited sub-question {i}?", "search_query": f"q {i}"} for i in range(6)]
    approve_plan(s, m, six, "Dana", cfg)
    assert m.max_steps == 20  # 24 needed, capped by max_max_steps
    other = _planned(s, cfg, tools)
    approve_plan(s, other, six[:2], "Dana", cfg)
    assert other.max_steps == 8


def test_a_budget_stop_is_partial_and_lists_the_sub_questions_that_never_ran(s, cfg, tools, llm):
    llm.handlers["mission_plan"] = _plan(4)
    n = {"extract": 0}

    def extract(call: Any) -> dict[str, Any]:
        n["extract"] += 1
        if n["extract"] == 3:  # during Q3
            raise MissionBudgetExceeded("mission budget reached: spent $0.30 of $0.30")
        return default_extract(call)

    llm.handlers["mission_extract"] = extract
    m = _planned(s, cfg, tools)
    approve(s, m)
    m = _research(s, cfg, tools, m)

    assert m.status == MissionStatus.BUDGET_EXCEEDED
    assert m.report["partial"] is True
    assert [q["n"] for q in m.report["skipped"]] == [3, 4]
    assert m.report["skipped"][1]["question"].startswith("How does this compare with JFrog")


def test_a_forced_step_limit_never_reads_as_a_plain_completed(s, cfg, tools, llm):
    llm.handlers["mission_plan"] = _plan(4)
    m = _planned(s, cfg, tools)
    approve(s, m, max_steps=12)  # e.g. a cap below what the plan needs
    m = _research(s, cfg, tools, m)

    assert m.status == MissionStatus.COMPLETED
    assert "Step limit reached (12 steps)" in m.report["stopped_reason"]
    assert m.report["partial"] is True and [q["n"] for q in m.report["skipped"]] == [4]


def test_stop_by_the_analyst_is_partial(s, cfg, tools, llm):
    llm.handlers["mission_plan"] = _plan(4)
    m = _planned(s, cfg, tools)
    approve(s, m)
    calls = {"n": 0}

    def extract(call: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:  # the analyst presses Stop while Q1 is extracted
            s.get(Mission, m.id).stop_requested = True
            s.flush()
        return default_extract(call)

    llm.handlers["mission_extract"] = extract
    m = _research(s, cfg, tools, m)
    assert m.status == MissionStatus.CANCELLED
    assert m.report["partial"] is True and [q["n"] for q in m.report["skipped"]] == [1, 2, 3, 4]


# ---------- API ----------


@pytest.fixture
def client(s: Session, llm: ScriptedLLM) -> Any:
    from app.api.missions import require_mission_keys
    from app.db.session import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: s
    app.dependency_overrides[require_mission_keys] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_shows_partial_and_the_skipped_list_also_for_older_missions(client, s, cfg, tools,
                                                                        llm):  # fmt: skip
    llm.handlers["mission_plan"] = _plan(4)
    m = _planned(s, cfg, tools)
    approve(s, m, max_steps=12)
    m = _research(s, cfg, tools, m)
    # A mission from before D84 has no report.skipped: the API computes it from the steps.
    m.report = {k: v for k, v in m.report.items() if k not in ("partial", "skipped")}
    s.commit()

    d = client.get(f"/api/missions/{m.id}").json()
    assert d["status"] == "completed" and d["partial"] is True
    assert (d["sub_questions_total"], d["sub_questions_researched"]) == (4, 3)
    assert [q["n"] for q in d["skipped"]] == [4]
    assert [q["n"] for q in d["report"]["skipped"]] == [4]
    (item,) = client.get("/api/missions").json()["items"]
    assert item["partial"] is True and item["skipped"][0]["n"] == 4
