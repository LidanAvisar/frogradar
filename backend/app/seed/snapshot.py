"""Seed data: load the committed intelligence snapshot into a fresh DB (no LLM calls) or export it.

    python -m app.seed.snapshot load      # make seed: a fresh DB gets the collected intelligence
    python -m app.seed.snapshot export [--as-of ISO]   # make seed-export (maintainers only)
    python -m app.seed.snapshot info

Format: backend/app/seed/data/data.tar.gz (one CSV per table, from COPY) + manifest.json (as-of
time, alembic revision, row counts). It lives under app/ so the backend image carries it.

Seeded rows are normal data: the app reads them with the real clock (D83). The Brief shows the
latest available day with its date when nothing is newer (app/api/brief.py).

Stripped on export (never shown, and the pipeline doesn't re-process seeded rows; D25 / D77):
signals.raw_text, page_snapshots.text, mission_sources.excerpt → '' (rows flagged
meta.snapshot_stripped where the table has meta); embeddings of signals outside the active sources
(Ask only searches in-scope signals). Not loaded: llm_cache (the seed carries intelligence and its
history, not cached model responses) and alembic_version.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from app.db.session import get_engine

SNAPSHOT_DIR = Path(__file__).resolve().parent / "data"
MANIFEST = SNAPSHOT_DIR / "manifest.json"
DATA = SNAPSHOT_DIR / "data.tar.gz"
FORMAT_VERSION = 1
SKIP_TABLES = {"alembic_version", "llm_cache"}  # never exported or loaded
# Filled by the config seed (app.db.seed) at every start: replaced by the snapshot's own rows.
CONFIG_TABLES = ("entities", "sources", "capabilities", "capability_matrix")

# Column → SQL expression used on export instead of the column itself.
_IN_SCOPE = (
    "(t.source_id IS NULL OR EXISTS (SELECT 1 FROM sources s WHERE s.id = t.source_id "
    "AND s.is_active))"
)
_STRIPPED_META = "(t.meta || '{\"snapshot_stripped\": true}'::jsonb)"
REPLACE: dict[str, dict[str, str]] = {
    "signals": {
        "raw_text": "''",
        "embedding": f"CASE WHEN {_IN_SCOPE} THEN t.embedding END",
        "meta": _STRIPPED_META,
    },
    "page_snapshots": {"text": "''", "meta": _STRIPPED_META},
    "mission_sources": {"excerpt": "''"},
}
WHERE: dict[str, str] = {}
STRIPPED = [
    "signals.raw_text",
    "page_snapshots.text",
    "mission_sources.excerpt",
    "signals.embedding (outside the active sources)",
    "llm_cache (not exported)",
]


class SnapshotError(RuntimeError):
    pass


def alembic_head() -> str:
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "db" / "migrations")
    )
    return ScriptDirectory.from_config(cfg).get_current_head() or ""


def _tables(conn) -> list[str]:
    """Tables in FK order (parents first); fine for COPY with triggers on or off."""
    rows = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname"
        )
    ).scalars()
    names = [r for r in rows if r not in SKIP_TABLES]
    deps = {t: set() for t in names}
    for child, parent in conn.execute(
        text(
            "SELECT tc.relname, tp.relname FROM pg_constraint k "
            "JOIN pg_class tc ON tc.oid = k.conrelid JOIN pg_class tp ON tp.oid = k.confrelid "
            "WHERE k.contype = 'f'"
        )
    ):
        if child in deps and parent in deps and child != parent:
            deps[child].add(parent)
    ordered: list[str] = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not (d - set(ordered)))
        if not ready:  # a cycle: append the rest alphabetically (restore disables triggers)
            ready = sorted(deps)
        for t in ready:
            ordered.append(t)
            deps.pop(t)
    return ordered


def _columns(conn, table: str) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND is_generated = 'NEVER' ORDER BY ordinal_position"
            ),
            {"t": table},
        ).scalars()
    )


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def export(engine: Engine | None = None, *, as_of: datetime | None = None,
           out_dir: Path = SNAPSHOT_DIR) -> dict:  # fmt: skip
    engine = engine or get_engine()
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    columns: dict[str, list[str]] = {}
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if as_of is None:
            as_of = conn.execute(text("SELECT max(finished_at) FROM pipeline_runs")).scalar()
        if as_of is None:
            raise SnapshotError("no finished pipeline run to take the as-of time from (--as-of)")
        tables = _tables(conn)
        raw = conn.connection.dbapi_connection
        data_path = out_dir / DATA.name
        with tarfile.open(data_path, "w:gz", compresslevel=9) as tar:
            for table in tables:
                cols = _columns(conn, table)
                exprs = [
                    f"{REPLACE.get(table, {}).get(c, 't.' + _quote(c))} AS {_quote(c)}"
                    for c in cols
                ]
                where = f" WHERE {WHERE[table]}" if table in WHERE else ""
                order = " ORDER BY t.id" if "id" in cols else ""
                sql = f"SELECT {', '.join(exprs)} FROM {_quote(table)} t{where}{order}"
                buf = io.BytesIO()
                with (
                    raw.cursor() as cur,
                    cur.copy(f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy,
                ):
                    for chunk in copy:
                        buf.write(chunk)
                data = buf.getvalue()
                # Rows, not lines: CSV fields can contain newlines.
                counts[table] = sum(1 for _ in csv.reader(io.StringIO(data.decode()))) - 1
                columns[table] = cols
                info = tarfile.TarInfo(f"{table}.csv")
                info.size = len(data)
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
    manifest = {
        "format": FORMAT_VERSION,
        "as_of": as_of.astimezone(UTC).isoformat(),
        "exported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "alembic_revision": revision,
        "tables": tables,
        "columns": columns,
        "row_counts": counts,
        "stripped": STRIPPED,
        "size_bytes": data_path.stat().st_size,
    }
    (out_dir / MANIFEST.name).write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def _non_config_rows(conn, tables: list[str]) -> dict[str, int]:
    """Row counts of tables that hold more than config (a DB that is already in use)."""
    out = {}
    for table in tables:
        if table in CONFIG_TABLES:
            continue
        n = conn.execute(text(f"SELECT count(*) FROM {_quote(table)}")).scalar_one()
        if n:
            out[table] = n
    return out


def load(engine: Engine | None = None, *, snapshot_dir: Path = SNAPSHOT_DIR,
         sync_config: bool = True) -> str:  # fmt: skip
    """Load the snapshot into a migrated DB that holds nothing but config rows (a fresh `make up`).
    Refuses a DB that is already in use. Then re-applies config/ (competitors, sources,
    capabilities may have changed since the export). Returns a one-line report."""
    engine = engine or get_engine()
    manifest_path, data_path = snapshot_dir / MANIFEST.name, snapshot_dir / DATA.name
    if not manifest_path.exists() or not data_path.exists():
        raise SnapshotError(f"no seed data in {snapshot_dir}")
    manifest = json.loads(manifest_path.read_text())
    tables = [t for t in manifest["tables"] if t not in SKIP_TABLES]
    with engine.begin() as conn:
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if head != manifest["alembic_revision"]:
            raise SnapshotError(
                f"the seed data is at migration {manifest['alembic_revision']}, the DB at {head}: "
                "re-export it (make seed-export) after a schema change"
            )
        in_use = _non_config_rows(conn, _tables(conn))
        if in_use:
            listed = ", ".join(f"{t} {n}" for t, n in sorted(in_use.items()))
            raise SnapshotError(
                f"the database already has data ({listed}); the seed only loads into a fresh one. "
                "To start over (deletes this database): make down, then "
                "docker compose down -v, make up, make seed"
            )
        conn.execute(text(f"TRUNCATE {', '.join(_quote(t) for t in CONFIG_TABLES)} CASCADE"))
        raw = conn.connection.dbapi_connection
        with raw.cursor() as cur:
            try:  # FK / trigger checks off while loading (superuser; compose's DB user is one)
                cur.execute("SAVEPOINT srr")
                cur.execute("SET LOCAL session_replication_role = replica")
            except Exception:  # noqa: BLE001 - not a superuser: rely on the FK order
                cur.execute("ROLLBACK TO SAVEPOINT srr")
        with tarfile.open(data_path, "r:gz") as tar:
            for table in tables:
                member = tar.extractfile(f"{table}.csv")
                if member is None:
                    raise SnapshotError(f"{table}.csv missing from {data_path}")
                cols = ", ".join(_quote(c) for c in manifest["columns"][table])
                with (
                    raw.cursor() as cur,
                    cur.copy(
                        f"COPY {_quote(table)} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)"
                    ) as copy,
                ):
                    while chunk := member.read(1 << 20):
                        copy.write(chunk)
        # Identity sequences continue after the loaded ids.
        for table, col in conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND is_identity = 'YES'"
            )
        ).all():
            conn.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence(:t, :c), "
                    f"coalesce((SELECT max({_quote(col)}) FROM {_quote(table)}), 0) + 1, false)"
                ),
                {"t": table, "c": col},
            )
    if sync_config:
        from sqlalchemy.orm import Session

        from app.db.seed import seed

        with Session(engine) as session, session.begin():
            seed(session)
    total = sum(n for t, n in manifest["row_counts"].items() if t in tables)
    day = manifest["as_of"][:10]
    return (
        f"seed loaded: {total} rows in {len(tables)} tables, intelligence up to {day}. "
        "Run `make pipeline` for today's news."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.seed.snapshot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("load", help="seed data → this DB (migrated, nothing but config rows)")
    e = sub.add_parser("export", help="this DB → backend/app/seed/data/ (maintainers)")
    e.add_argument("--as-of", help="ISO time (default: the latest finished pipeline run)")
    sub.add_parser("info", help="print the manifest summary")
    args = p.parse_args(argv)
    try:
        if args.cmd == "export":
            as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
            if as_of and as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=UTC)
            m = export(as_of=as_of)
            print(
                f"seed data exported: {sum(m['row_counts'].values())} rows, "
                f"{m['size_bytes'] / 1e6:.2f} MB, as of {m['as_of']}, migration "
                f"{m['alembic_revision']}"
            )
        elif args.cmd == "load":
            print(load())
        else:
            m = json.loads(MANIFEST.read_text())
            print(json.dumps({k: m[k] for k in m if k != "columns"}, indent=1))
    except SnapshotError as err:
        print(f"make seed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
