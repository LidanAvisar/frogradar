"""Shared fixtures. DB tests run against the docker-compose Postgres, each in a throwaway database.

If Postgres is unreachable, DB tests are skipped, unless REQUIRE_DB=1 (CI), where they fail.
"""

import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core import catalog
from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


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
