"""Later stages only process signals whose source is active (or that have no source)."""

import pytest
from sqlalchemy import select, update

from app.db.enums import FetchMethod, SourceKind
from app.db.models import Signal, Source
from app.pipeline.scope import count_in_scope, in_scope_signals

pytestmark = pytest.mark.db


def _signal(n: int, source_id: int | None) -> Signal:
    return Signal(
        source_id=source_id, url=f"https://x.test/{n}", canonical_url=f"https://x.test/{n}",
        content_hash=f"{n:064d}", title=f"t{n}", raw_text=f"text {n}",
    )  # fmt: skip


def test_only_signals_from_active_sources_or_without_source(collect_db):
    with collect_db.begin() as s:
        common = {"kind": SourceKind.PRESS, "fetch_method": FetchMethod.RSS, "trust_score": 0.7}
        active = Source(name="A", url="https://a.test/feed", **common)
        inactive = Source(name="B", url="https://b.test/feed", is_active=False, **common)
        s.add_all([active, inactive])
        s.flush()
        s.add_all([_signal(1, active.id), _signal(2, inactive.id), _signal(3, None)])
        active_id = active.id

    with collect_db() as s:
        assert {sig.title for sig in s.scalars(in_scope_signals())} == {"t1", "t3"}
        assert count_in_scope(s) == (2, 3)
        # Composes with a stage's own filters.
        assert s.scalars(in_scope_signals().where(Signal.title == "t2")).all() == []

    # Deactivating a source takes its signals out of scope without deleting them.
    with collect_db.begin() as s:
        s.execute(update(Source).where(Source.id == active_id).values(is_active=False))
    with collect_db() as s:
        assert count_in_scope(s) == (1, 3)
        assert len(s.scalars(select(Signal)).all()) == 3
