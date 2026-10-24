import json

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
    "radar_candidates", "repo_star_snapshots", "ask_conversations", "ask_turns",
    "mission_sources", "mission_findings",
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


ITEM = "Counter Nexus Pro+/Enterprise block-at-entry claims with JFrog Curation's policy gate."
CUT = "Sonatype battlecard · Opportunity for JFrog: Counter Nexus Pro+/Enterprise block-at-entry…"
CARD_AT = "2026-10-24T15:52:29.211769Z"


def _bc_source(date: str) -> dict:
    return {"n": 2, "id": "bc:sonatype:opportunities_for_jfrog:2", "type": "battlecard",
            "date": date, "url": "/battlecards/sonatype", "title": CUT}  # fmt: skip


def test_0015_repairs_cut_battlecard_titles_in_stored_answers(make_database):
    url = make_database()
    cfg = alembic_config(url)
    engine = create_engine(url)
    content = {"opportunities_for_jfrog": [{"text": "First.", "signal_ids": [1]},
                                           {"text": ITEM, "signal_ids": [2]}]}  # fmt: skip
    answer = {"sources": [
        _bc_source(CARD_AT),  # repaired from the card created at that time
        _bc_source("2026-10-20T00:00:00Z"),  # no card at that time: kept as it was
        {"n": 3, "id": "sig:5", "type": "article", "title": "An article…"},  # not a battlecard
    ]}  # fmt: skip
    try:
        command.upgrade(cfg, "0014")
        with engine.begin() as conn:
            ent = conn.execute(text(
                "INSERT INTO entities (type, name, slug, relationship) "
                "VALUES ('company', 'Sonatype', 'sonatype', 'competitor') RETURNING id"
            )).scalar_one()  # fmt: skip
            conn.execute(
                text("INSERT INTO reports (kind, entity_id, model, content, created_at) "
                     "VALUES ('battlecard', :e, 'm', CAST(:c AS jsonb), :at)"),
                {"e": ent, "c": json.dumps(content), "at": CARD_AT},
            )  # fmt: skip
            conv = conn.execute(
                text("INSERT INTO ask_conversations (title) VALUES ('q') RETURNING id")
            ).scalar_one()
            conn.execute(
                text("INSERT INTO ask_turns (conversation_id, position, question, "
                     "standalone_question, answer) VALUES (:c, 1, 'q', 'q', CAST(:a AS jsonb))"),
                {"c": conv, "a": json.dumps(answer)},
            )  # fmt: skip
        command.upgrade(cfg, "0015")
        with engine.connect() as conn:
            stored = conn.execute(text("SELECT answer FROM ask_turns")).scalar_one()
        full = f"Sonatype battlecard · Opportunity for JFrog: {ITEM}"
        assert [s["title"] for s in stored["sources"]] == [full, CUT, "An article…"]
    finally:
        engine.dispose()
