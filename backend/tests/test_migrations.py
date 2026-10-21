import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Base
from tests.conftest import alembic_config

pytestmark = pytest.mark.db

APP_TABLES = {
    "entities", "sources", "signals", "signal_entities", "signal_capabilities", "insights",
    "insight_signals",
    "feedback", "missions", "mission_steps", "capabilities", "capability_matrix",
    "page_snapshots", "watch_topics", "pipeline_runs", "llm_calls", "reports", "llm_cache",
}  # fmt: skip


def _tables(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _has_vector_extension(conn) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar())


def test_upgrade_downgrade_upgrade(make_database):
    url = make_database()
    cfg = alembic_config(url)
    engine = create_engine(url)
    try:
        command.upgrade(cfg, "head")
        assert _tables(engine) == APP_TABLES | {"alembic_version"}
        with engine.connect() as conn:
            assert _has_vector_extension(conn)

        command.downgrade(cfg, "base")
        assert _tables(engine) - {"alembic_version"} == set()
        with engine.connect() as conn:
            assert not _has_vector_extension(conn)

        command.upgrade(cfg, "head")
        assert _tables(engine) == APP_TABLES | {"alembic_version"}
    finally:
        engine.dispose()


def test_models_match_migrations(migrated_engine):
    """Autogenerate finds nothing to do: models and migrations haven't drifted apart."""
    with migrated_engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_signal_indexes(migrated_engine):
    with migrated_engine.connect() as conn:
        defs = dict(
            conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'signals'")
            ).all()
        )
    assert "USING hnsw (embedding vector_cosine_ops)" in defs["ix_signals_embedding_hnsw"]
    assert "USING gin (search_tsv)" in defs["ix_signals_search_tsv"]
    assert "UNIQUE" in defs["uq_signals_content_hash"]
    for col in ("published_at", "status"):
        assert f"btree ({col})" in defs[f"ix_signals_{col}"]


def test_embedding_column_dimension(migrated_engine):
    with migrated_engine.connect() as conn:
        typ = conn.execute(
            text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                 "WHERE attrelid = 'signals'::regclass AND attname = 'embedding'")
        ).scalar()  # fmt: skip
    assert typ == "vector(1536)"


def test_timestamps_are_timezone_aware(migrated_engine):
    with migrated_engine.connect() as conn:
        naive = conn.execute(
            text("SELECT table_name, column_name FROM information_schema.columns "
                 "WHERE table_schema = 'public' AND data_type = 'timestamp without time zone'")
        ).all()  # fmt: skip
    assert naive == []


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO entities (type, name, slug) VALUES ('planet', 'X', 'x')",
        "INSERT INTO entities (type, name, slug, segments) VALUES ('company', 'X', 'x', '{space}')",
        "INSERT INTO sources (name, url, kind, fetch_method, trust_score) "
        "VALUES ('s', 'https://x', 'official', 'rss', 1.5)",
        "INSERT INTO signals (url, canonical_url, content_hash, title, raw_text, impact) "
        "VALUES ('u', 'u', repeat('a', 64), 't', 'r', 6)",
    ],
    ids=["bad-enum", "bad-segment", "trust-range", "impact-range"],
)
def test_check_constraints(db_session, sql):
    with pytest.raises(IntegrityError):
        db_session.execute(text(sql))
        db_session.flush()


def test_only_one_self_entity(db_session):
    db_session.execute(
        text("INSERT INTO entities (type, name, slug, is_self) VALUES ('company', 'A', 'a', true)")
    )
    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO entities (type, name, slug, is_self) "
                "VALUES ('company', 'B', 'b', true)"
            )
        )
