"""Shared fixtures. DB tests run against the docker-compose Postgres, each in a throwaway database.

If Postgres is unreachable, DB tests are skipped, unless REQUIRE_DB=1 (CI), where they fail.
"""

import os

# Before anything imports LiteLLM: no implicit .env loading, no price-table download.
os.environ["LITELLM_MODE"] = "PRODUCTION"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import shutil  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import litellm  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import Engine, create_engine, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import catalog  # noqa: E402
from app.core.config import get_settings  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]

PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "TAVILY_API_KEY")
_LITELLM_ENTRYPOINTS = ("completion", "acompletion", "embedding", "aembedding")


# ---------- no real provider calls outside `live` tests ----------


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Live tests need two opt-ins: `-m live` and RUN_LIVE_TESTS=1 (set by `make test-live`)."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live test: run with `make test-live` (sets RUN_LIVE_TESTS=1)")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_real_providers(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-live tests run without provider keys, and any real LiteLLM call fails the test.

    Tests that need LiteLLM behaviour patch these functions again with fakes (their fixtures run
    after this autouse one). pytest.fail raises a BaseException, so the gateway's retry loop
    can't swallow it.
    """
    if request.node.get_closest_marker("live"):
        return
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)

    def _guard(name: str) -> Callable[..., None]:
        def guard(*args: object, **kwargs: object) -> None:
            pytest.fail(f"real provider call: litellm.{name}() in a non-live test")

        return guard

    for name in _LITELLM_ENTRYPOINTS:
        monkeypatch.setattr(litellm, name, _guard(name))


def _admin_url() -> URL:
    return make_url(get_settings().database_url).set(database="postgres")


@pytest.fixture(scope="session")
def pg_available() -> None:
    try:
        engine = create_engine(_admin_url(), connect_args={"connect_timeout": 3})
        with engine.connect():
            pass
        engine.dispose()
    except OperationalError as e:
        msg = f"Postgres not reachable (start it with `make db-up`): {type(e).__name__}"
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(msg)
        pytest.skip(msg)


@pytest.fixture(scope="session")
def make_database(pg_available: None) -> Iterator[Callable[[], URL]]:
    """Factory: creates an empty database and returns its URL. All are dropped at session end."""
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    created: list[str] = []

    def _make() -> URL:
        name = f"frogradar_test_{uuid.uuid4().hex[:10]}"
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return make_url(get_settings().database_url).set(database=name)

    yield _make

    with admin.connect() as conn:
        for name in created:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def alembic_config(url: URL) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "app/db/migrations"))
    # Escape % for configparser interpolation.
    cfg.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def migrated_engine(make_database: Callable[[], URL]) -> Iterator[Engine]:
    """One database migrated to head, shared by tests that only read/write rows."""
    url = make_database()
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """Session inside an outer transaction that is rolled back after the test."""
    with migrated_engine.connect() as conn:
        outer = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            outer.rollback()


@pytest.fixture
def config_copy(tmp_path: Path) -> Path:
    """A writable copy of config/ for tests that edit YAML."""
    dst = tmp_path / "config"
    shutil.copytree(get_settings().config_dir, dst)
    return dst


@pytest.fixture(autouse=True)
def _fresh_config_cache() -> Iterator[None]:
    yield
    catalog.clear_cache()
