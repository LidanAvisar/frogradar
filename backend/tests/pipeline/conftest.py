"""Fixtures for collector tests: a fake web (httpx.MockTransport), a polite client, a DB."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.pipeline.collectors import PoliteClient
from tests.conftest import alembic_config

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

Response = httpx.Response | Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeWeb:
    """Routes by full URL, then without query. Unknown URLs (incl. robots.txt) → 404."""

    def __init__(self) -> None:
        self.routes: dict[str, Response] = {}
        self.requests: list[httpx.Request] = []

    def add(self, url: str, body: bytes | str = b"", status: int = 200, **headers: str) -> None:
        content = body.encode() if isinstance(body, str) else body
        hdrs = {k.replace("_", "-"): v for k, v in headers.items()}
        self.routes[url] = lambda req: httpx.Response(status, content=content, headers=hdrs)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        route = self.routes.get(url) or self.routes.get(url.split("?", 1)[0])
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def count(self, url: str) -> int:
        return sum(u == url for u in self.urls())


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "collector_domain_delay_s": 1.0,
        "collector_timeout_s": 5.0,
        "github_token": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(web: FakeWeb, clock: FakeClock) -> Iterator[Callable[..., PoliteClient]]:
    clients: list[PoliteClient] = []

    def _make(**settings: Any) -> PoliteClient:
        client = PoliteClient(
            make_settings(**settings),
            transport=httpx.MockTransport(web.handler),
            clock=clock,
            sleep=clock.sleep,
        )
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.close()


@pytest.fixture(scope="session")
def collect_engine(make_database: Callable[[], URL]) -> Iterator[Engine]:
    """A database of its own: the run commits, so rows must not leak into other tests."""
    url = make_database()
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def collect_db(collect_engine: Engine) -> Iterator[sessionmaker[Session]]:
    yield sessionmaker(bind=collect_engine, expire_on_commit=False)
    with collect_engine.begin() as conn:
        conn.execute(text("TRUNCATE signals, sources, entities, pipeline_runs CASCADE"))
