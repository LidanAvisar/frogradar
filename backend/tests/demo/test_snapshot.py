"""Demo snapshot export / restore (D77): stripping, round trip, empty-DB and migration guards."""

import json
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.clock import MANIFEST
from app.db.seed import seed
from app.demo.snapshot import SnapshotError, alembic_head, export, restore
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


def test_round_trip_strips_and_restores(fresh_engine, tmp_path):
    src = fresh_engine()
    _fill(src)
    m = export(src, out_dir=tmp_path)
    assert m["as_of"].startswith("2026-10-21T17:33")
    assert m["alembic_revision"] == alembic_head()
    assert m["row_counts"]["signals"] == 2 and m["row_counts"]["llm_cache"] == 1

    dst = fresh_engine()
    report = restore(dst, snapshot_dir=tmp_path)
    assert "snapshot restored" in report
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
        assert c.execute(text("SELECT operation FROM llm_cache")).scalars().all() == ["ask"]
    # Identity sequences continue after the restored ids.
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


def test_restore_skips_a_db_with_data(fresh_engine, tmp_path):
    src = fresh_engine()
    _fill(src)
    export(src, out_dir=tmp_path)
    assert "not restored" in restore(src, snapshot_dir=tmp_path, if_empty=True)
    with pytest.raises(SnapshotError, match="not empty"):
        restore(src, snapshot_dir=tmp_path, if_empty=False)


def test_restore_refuses_another_migration(fresh_engine, tmp_path):
    src = fresh_engine()
    _fill(src)
    export(src, out_dir=tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["alembic_revision"] = "0001"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError, match="re-export"):
        restore(fresh_engine(), snapshot_dir=tmp_path)


def test_committed_snapshot_matches_the_schema():
    if not MANIFEST.exists():
        pytest.skip("no committed snapshot yet")
    m = json.loads(MANIFEST.read_text())
    assert m["alembic_revision"] == alembic_head(), "re-export: make demo-snapshot"
    assert datetime.fromisoformat(m["as_of"]) < datetime.now(UTC)
    assert m["size_bytes"] < 15_000_000


def test_committed_snapshot_replays_the_demo_without_provider_calls():
    """make demo-check: restore into a throwaway DB, demo mode, pages + cached Ask + MCP."""
    if not MANIFEST.exists():
        pytest.skip("no committed snapshot yet")
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "app.demo.check"], capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "demo-check: OK" in r.stdout
