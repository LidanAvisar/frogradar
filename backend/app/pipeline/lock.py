"""One pipeline run at a time: a PostgreSQL session-level advisory lock (D42).

Held on a dedicated connection for the whole run. If the process dies, Postgres drops the
connection and the lock with it, so a crashed run can never block the next one (unlike a
"running" row in pipeline_runs, which would need a staleness timeout).
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

# Arbitrary constant shared by every process that runs the pipeline ("FROG" in ASCII).
PIPELINE_LOCK_KEY = 0x46524F47


class PipelineLocked(RuntimeError):
    """Another pipeline run (scheduled, manual or backfill) holds the lock."""


@contextmanager
def pipeline_lock(engine: Engine, key: int = PIPELINE_LOCK_KEY) -> Iterator[None]:
    conn = engine.connect()
    try:
        if not conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}):
            raise PipelineLocked("another pipeline run is in progress")
        conn.commit()  # session-level lock survives; don't sit idle in a transaction
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            conn.commit()
    finally:
        conn.close()
