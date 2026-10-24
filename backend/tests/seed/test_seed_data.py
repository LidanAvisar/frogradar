"""Seed data (D77, D83): export / load round trip, stripping, guards, and the committed seed
served by the real API with the real clock and no provider calls."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.db.models import Signal
from app.db.seed import seed
from app.pipeline.scope import event_time, not_mission, usable
from app.seed.snapshot import MANIFEST, SnapshotError, alembic_head, export, load
from tests.conftest import alembic_config

pytestmark = pytest.mark.db


@pytest.fixture
def fresh_engine(make_database):
    def _make():
        url = make_database()
        command.upgrade(alembic_config(url), "head")
        engine = create_engine(url)
        engines.append(engine)
        return engine

    engines = []
    yield _make
    for e in engines:
        e.dispose()


def _fill(engine) -> None:
    with Session(engine) as s:
        seed(s)
        s.commit()
    with engine.begin() as c:
        active, inactive = c.execute(
            text(
                "SELECT (SELECT id FROM sources WHERE is_active ORDER BY id LIMIT 1), "
                "(SELECT id FROM sources WHERE NOT is_active ORDER BY id LIMIT 1)"
            )
        ).one()
        vec = "[" + ",".join(["0.1"] * 1536) + "]"
        for i, src in enumerate((active, inactive)):
            c.execute(
                text(
                    "INSERT INTO signals (source_id, url, canonical_url, content_hash, title, "
                    "raw_text, fetched_at, embedding, meta) VALUES (:s, :u, :u, :h, :t, "
                    "'Full copyrighted article body.', now(), CAST(:v AS vector), '{\"k\": 1}')"
                ),
                {"s": src, "u": f"https://x.test/{i}", "h": f"{i:064d}", "t": f"T{i}", "v": vec},
            )
        c.execute(
            text(
                "INSERT INTO pipeline_runs (trigger, status, started_at, finished_at) "
                "VALUES ('manual', 'succeeded', now(), '2026-10-21T17:33:00Z')"
            )
        )
        for op in ("embed_signal", "ask"):
            c.execute(
                text(
                    "INSERT INTO llm_cache (cache_key, kind, model, operation, response) "
                    "VALUES (:k, 'completion', 'm/x', :op, '{}')"
                ),
                {"k": op.ljust(64, "0"), "op": op},
            )


def _started(engine) -> None:
    """What the backend container does at every start: the config seed (no data yet)."""
    with Session(engine) as s:
        seed(s)
        s.commit()


def test_round_trip_strips_and_loads(fresh_engine, tmp_path):
    src = fresh_engine()
    _fill(src)
    m = export(src, out_dir=tmp_path)
    assert m["as_of"].startswith("2026-10-21T17:33")
    assert m["alembic_revision"] == alembic_head()
    assert m["row_counts"]["signals"] == 2 and "llm_cache" not in m["row_counts"]

    dst = fresh_engine()
    _started(dst)  # config rows already there, as after `make up`
    report = load(dst, snapshot_dir=tmp_path)
    assert "seed loaded" in report and "up to 2026-10-21" in report
    with dst.connect() as c:
        rows = c.execute(
            text(
                "SELECT s.raw_text, s.embedding IS NOT NULL, s.meta, so.is_active FROM signals s "
                "JOIN sources so ON so.id = s.source_id ORDER BY s.id"
            )
        ).all()
        assert [r[0] for r in rows] == ["", ""]  # never shipped
        assert {r[3]: r[1] for r in rows} == {True: True, False: False}  # in-scope keeps vector
        assert all(r[2] == {"k": 1, "snapshot_stripped": True} for r in rows)
        assert c.execute(text("SELECT count(*) FROM llm_cache")).scalar() == 0  # not seeded
    # Identity sequences continue after the loaded ids.
    with dst.begin() as c:
        max_id = c.execute(text("SELECT max(id) FROM signals")).scalar_one()
        new_id = c.execute(
            text(
                "INSERT INTO signals (url, canonical_url, content_hash, title, raw_text, "
                "fetched_at) VALUES ('u', 'u', :h, 't', 'x', now()) RETURNING id"
            ),
            {"h": "f" * 64},
        ).scalar_one()
        assert new_id > max_id
        # The generated full-text column was rebuilt from title + summary.
        assert (
            c.execute(text("SELECT count(*) FROM signals WHERE search_tsv IS NULL")).scalar() == 0
        )


def test_load_refuses_a_db_in_use(fresh_engine, tmp_path):
    src = fresh_engine()
    _fill(src)
    export(src, out_dir=tmp_path)
    with pytest.raises(SnapshotError, match="already has data .*signals 2.*make seed"):
        load(src, snapshot_dir=tmp_path)


def test_load_refuses_another_migration(fresh_engine, tmp_path):
    src = fresh_engine()
    _fill(src)
    export(src, out_dir=tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["alembic_revision"] = "0001"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError, match="re-export"):
        load(fresh_engine(), snapshot_dir=tmp_path)


def test_committed_seed_matches_the_schema():
    m = json.loads(MANIFEST.read_text())
    assert m["alembic_revision"] == alembic_head(), "re-export: make seed-export"


@pytest.fixture
def seeded(fresh_engine) -> Iterator:
    from fastapi.testclient import TestClient

    from app.api.deps import get_now
    from app.db.session import get_session
    from app.main import app

    engine = fresh_engine()
    _started(engine)
    load(engine)
    clock: dict[str, datetime] = {}

    def session() -> Iterator[Session]:
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_now] = lambda: clock["now"]
    try:
        yield engine, clock, TestClient(app)
    finally:
        app.dependency_overrides.clear()


PAGES = [
    "/api/brief", "/api/signals", "/api/battlecards", "/api/entities/sonatype/battlecard",
    "/api/radar", "/api/missions", "/api/review", "/api/nav", "/api/status",
    "/api/feedback/scorecard", "/api/ask/conversations",
]  # fmt: skip


def test_committed_seed_serves_every_screen_with_the_real_clock_and_no_llm_calls(seeded):
    engine, clock, client = seeded
    as_of = datetime.fromisoformat(json.loads(MANIFEST.read_text())["as_of"])
    with engine.connect() as c:
        calls_before = c.execute(text("SELECT count(*) FROM llm_calls")).scalar_one()

    # A day after the seed: a normal Brief (its news is within the 7-day window).
    clock["now"] = as_of.replace(hour=23)
    for page in PAGES:
        assert client.get(page).status_code == 200, page
    fresh = client.get("/api/brief").json()
    assert fresh["stale"] is False and fresh["developments"]

    # Weeks later, nothing newer: the latest available day with its date, never "today".
    clock["now"] = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    stale = client.get("/api/brief").json()
    assert stale["stale"] is True
    # Anchored on the newest rankable signal (its publish date), not on the export time.
    with engine.connect() as c:
        newest = c.execute(
            select(func.max(event_time())).select_from(Signal).where(
                usable(), not_mission(), Signal.impact.is_not(None),
                Signal.confidence.is_not(None),
            )
        ).scalar_one()  # fmt: skip
    assert stale["as_of"][:10] == newest.date().isoformat() <= as_of.date().isoformat()
    assert stale["top_story"] and stale["developments"]  # the seed's Top 5, not an empty page
    assert stale["generated_at"].startswith("2026-12-01")

    # Seeded Ask history displays with its citations (stored answers, no model call).
    conv = client.get("/api/ask/conversations").json()
    assert conv, "the seed carries Ask history"
    turns = []
    for c in conv:
        detail = client.get(f"/api/ask/conversations/{c['id']}")
        assert detail.status_code == 200
        assert "demo" not in detail.text.lower()
        turns += detail.json()["turns"]
    cited = [t for t in turns if not t["not_enough_evidence"]]
    assert cited and all(t["sources"] and t["points"] for t in cited)
    # Every cited point links to a source of its turn.
    for t in cited:
        numbers = {s["n"] for s in t["sources"]}
        assert all(p["citations"] and set(p["citations"]) <= numbers for p in t["points"])
        assert all(s["url"] for s in t["sources"])

    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM llm_calls")).scalar_one() == calls_before
