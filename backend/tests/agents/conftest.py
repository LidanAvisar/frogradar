"""Mission agent fixtures: a scripted LLM, fake web search / fetch, a fake clock, row builders."""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.mission.runner import Tools
from app.agents.mission.service import create_mission
from app.agents.mission.web import Page, SkipPage, WebResult
from app.core.catalog import MissionSettings
from app.db.enums import MissionOrigin, RadarKind, RadarStatus
from app.db.models import Mission, RadarCandidate
from tests.pipeline.factories import FakeEmbed

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
RAW_SENTINEL = "PAGETEXT-SENTINEL-91c2"

PAGE_A = (
    "Sonatype Lifecycle adds reachability analysis for Java. The new call-graph engine marks "
    "vulnerable methods that application code actually calls. It is available in Lifecycle "
    f"Enterprise from October 2026. {RAW_SENTINEL} More text follows here to make it long."
)
PAGE_B = (
    "Sonatype announced a partnership with a cloud vendor for repository hosting. The service runs "
    f"in three regions. Pricing starts at $2,000 per year. {RAW_SENTINEL} Filler text for length."
)


@dataclass
class Call:
    operation: str
    task: str
    messages: list[dict[str, Any]]


def _keys(call: Call) -> list[str]:
    return re.findall(r"Finding (F\d+):", call.messages[-1]["content"])


def default_plan(call: Call) -> dict[str, Any]:
    return {
        "title": "Sonatype reachability",
        "sub_questions": [
            {"question": f"Question number {i}?", "search_query": f"sonatype query {i}"}
            for i in (1, 2, 3)
        ],
    }


def default_extract(call: Call) -> dict[str, Any]:
    body = call.messages[-1]["content"]
    if "reachability analysis" in body:
        quote, statement = (
            "Sonatype Lifecycle adds reachability analysis for Java.",
            "Sonatype Lifecycle now offers reachability analysis for Java applications.",
        )
    else:
        quote, statement = (
            "Pricing starts at $2,000 per year.",
            "Sonatype's hosted repository service starts at $2,000 per year.",
        )
    return {
        "findings": [
            {
                "statement": statement,
                "source": 1,
                "evidence_quote": quote,
                "confidence": "high",
                "impact": 3,
                "category": "feature_update",
                "why_jfrog_cares": "Competes with Xray.",
            }
        ],  # fmt: skip
        "not_found": "",
    }


def supported(call: Call) -> dict[str, Any]:
    return {"items": [{"key": k, "verdict": "supported", "note": "Stated."} for k in _keys(call)]}


def default_report(call: Call) -> dict[str, Any]:
    keys = re.findall(r"^(F\d+) \(", call.messages[-1]["content"], re.MULTILINE)
    return {
        "summary": [
            {"text": "Sonatype offers reachability analysis.", "findings": keys[:1]},
            {"text": "Uncited point.", "findings": ["F999999"]},
        ],  # fmt: skip
        "open_questions": ["Is it available for npm?"],
    }


class ScriptedLLM:
    def __init__(self, **handlers: Any) -> None:
        self.handlers: dict[str, Any] = {
            "mission_plan": default_plan,
            "mission_extract": default_extract,
            "mission_verify": supported,
            "mission_report": default_report,
            **handlers,
        }
        self.calls: list[Call] = []

    def __call__(self, task, operation, messages, response_model, *, prompt_version=None, **_):  # noqa: ANN001, ANN204
        call = Call(operation, task, list(messages))
        self.calls.append(call)
        h = self.handlers[operation]
        data = h(call) if callable(h) else h
        return response_model.model_validate(data)

    def ops(self) -> list[str]:
        return [c.operation for c in self.calls]


@dataclass
class FakeSearch:
    results: list[WebResult] = field(
        default_factory=lambda: [
            WebResult("Sonatype blog", "https://www.sonatype.com/blog/reachability", "..."),
            WebResult("Partner news", "https://news.example.com/sonatype-hosting", "..."),
        ]
    )
    queries: list[str] = field(default_factory=list)

    def __call__(self, query: str, n: int, window: str | None) -> list[WebResult]:
        self.queries.append(query)
        return self.results[:n]


@dataclass
class FakeFetch:
    pages: dict[str, str] = field(
        default_factory=lambda: {
            "https://www.sonatype.com/blog/reachability": PAGE_A,
            "https://news.example.com/sonatype-hosting": PAGE_B,
        }
    )
    errors: dict[str, Exception] = field(default_factory=dict)
    fetched: list[str] = field(default_factory=list)
    hook: Callable[[str], None] | None = None

    def __call__(self, url: str) -> Page:
        self.fetched.append(url)
        if self.hook:
            self.hook(url)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.pages:
            raise SkipPage("no readable article text")
        return Page(url=url, title=url.rsplit("/", 1)[-1], text=self.pages[url])


class FakeClock:
    def __init__(self, step_s: float = 0.0) -> None:
        self.now = NOW
        self.step = timedelta(seconds=step_s)

    def __call__(self) -> datetime:
        self.now += self.step
        return self.now


@pytest.fixture
def s(db_session: Session) -> Iterator[Session]:
    db_session.execute(
        text(
            "TRUNCATE missions, signals, sources, entities, radar_candidates, feedback, llm_calls "
            "RESTART IDENTITY CASCADE"
        )
    )
    yield db_session


@pytest.fixture
def cfg() -> MissionSettings:
    return MissionSettings(kb_results=0, web_results=2, pages_per_question=2)


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def search() -> FakeSearch:
    return FakeSearch()


@pytest.fixture
def fetch() -> FakeFetch:
    return FakeFetch()


@pytest.fixture
def tools(llm: ScriptedLLM, search: FakeSearch, fetch: FakeFetch) -> Tools:
    return Tools(complete=llm, embed=FakeEmbed(), search=search, fetch=fetch, clock=FakeClock())


def make_mission(s: Session, cfg: MissionSettings, **kw: Any) -> Mission:
    args: dict[str, Any] = {
        "brief": "Does Sonatype offer reachability analysis?",
        "created_by": "Dana",
        "origin_kind": MissionOrigin.MANUAL,
        "origin_ref": {},
        "company_ids": [],
        "days": None,
        "budget_usd": 0.30,
        "cfg": cfg,
    }
    args.update(kw)
    m = create_mission(s, **args)
    s.commit()
    return m


def approve(s: Session, m: Mission, questions: int | None = None,
            max_steps: int | None = None) -> None:  # fmt: skip
    """Approve the plan (only its first `questions` sub-questions if given). The step limit then
    follows the plan (D84); `max_steps` forces a lower one, to test a cut-short mission."""
    from app.agents.mission.service import approve_plan

    s.refresh(m)
    subs = None
    if questions is not None:
        subs = [dict(q) for q in m.plan["sub_questions"][:questions]]
    approve_plan(s, m, subs, "Dana")
    if max_steps is not None:
        m.max_steps = max_steps
    s.commit()


def make_candidate(s: Session, name: str = "GitGuardian") -> RadarCandidate:
    c = RadarCandidate(
        name=name, key=name.casefold(), kind=RadarKind.COMPANY,
        first_seen=NOW - timedelta(days=5), last_seen=NOW - timedelta(days=1),
        mention_signal_ids=[], source_ids=[], segments=["supply_chain_security"],
        status=RadarStatus.NEW, score=40.0,
    )  # fmt: skip
    s.add(c)
    s.flush()
    return c
