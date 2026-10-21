"""Semantic dedup with synthetic vectors (no network): merge rules, canonical choice, stability."""

import pytest
from sqlalchemy import select, update

from app.db.models import Signal, Source
from app.pipeline.dedup import dedup_signals
from tests.pipeline.factories import ctx, rotated, signal, source, vec

pytestmark = pytest.mark.db


def _cluster(s, *sigs: Signal) -> list[int | None]:
    s.expire_all()
    return [s.get(Signal, x.id).dedup_cluster_id for x in sigs]


def test_cross_source_duplicates_merge(db_session):
    s = db_session
    a, b = source(s, "A"), source(s, "B")
    x = signal(s, a, "Sonatype buys X", embedding=rotated(0))
    y = signal(s, b, "Sonatype acquires X", embedding=rotated(20), days=1)  # sim 0.94
    z = signal(s, b, "Unrelated", embedding=vec(1.0, axis=5), days=1)

    counts = dedup_signals(s, ctx())

    assert _cluster(s, x, y, z) == [x.id, x.id, z.id]
    assert counts["joined"] == 1 and counts["new_clusters"] == 2
    assert counts["multi_signal_clusters"] == 1 and counts["duplicates"] == 1


def test_below_cross_threshold_not_merged(db_session):
    s = db_session
    a, b = source(s, "A"), source(s, "B")
    x = signal(s, a, embedding=rotated(0))
    y = signal(s, b, embedding=rotated(35))  # sim 0.82 < 0.85
    dedup_signals(s, ctx())
    assert _cluster(s, x, y) == [x.id, y.id]


def test_same_source_templated_entries_not_merged(db_session):
    """Changelog entries share a template: 0.93 similar, but different news."""
    s = db_session
    changelog = source(s, "GitHub Changelog", trust=0.9)
    x = signal(s, changelog, "Dependabot now supports npm 12", embedding=rotated(0))
    y = signal(s, changelog, "Dependabot now supports Bun 1.4", embedding=rotated(21), days=1)
    dedup_signals(s, ctx())
    assert _cluster(s, x, y) == [x.id, y.id]


def test_same_source_near_copy_merges(db_session):
    s = db_session
    blog = source(s, "Blog")
    x = signal(s, blog, embedding=rotated(0))
    y = signal(s, blog, embedding=rotated(5), days=1)  # sim 0.996 >= 0.97
    dedup_signals(s, ctx())
    assert _cluster(s, x, y) == [x.id, x.id]


def test_same_source_entries_not_bridged_by_third_article(db_session):
    """Two changelog entries both near one press article must not end up in one cluster."""
    s = db_session
    # The article is more trusted, so it becomes the canonical: c2 passes the canonical check
    # (cross-source) and is stopped only by the same-source member check against c1.
    changelog, press = source(s, "Changelog", trust=0.7), source(s, "Press", trust=0.9)
    c1 = signal(s, changelog, embedding=rotated(0))
    article = signal(s, press, embedding=rotated(10), days=1)  # 0.985 to c1
    c2 = signal(s, changelog, embedding=rotated(20), days=2)  # 0.985 to article, 0.94 to c1
    dedup_signals(s, ctx(cross=0.9))
    assert _cluster(s, c1, article, c2) == [article.id, article.id, c2.id]


def test_no_chaining_through_non_canonical_member(db_session):
    """A~B and B~C pass, A~C doesn't: C must not join A's cluster through B."""
    s = db_session
    sa, sb, sc = source(s, "A", trust=0.9), source(s, "B"), source(s, "C")
    a = signal(s, sa, embedding=rotated(0))
    b = signal(s, sb, embedding=rotated(25), days=1)  # A~B 0.906
    c = signal(s, sc, embedding=rotated(50), days=2)  # B~C 0.906, A~C 0.643
    dedup_signals(s, ctx())
    assert _cluster(s, a, b, c) == [a.id, a.id, c.id]


def test_canonical_is_most_trusted_then_earliest(db_session):
    s = db_session
    press, official = source(s, "Press", trust=0.7), source(s, "Official", trust=0.9)
    community = source(s, "Community", trust=0.5)
    early_press = signal(s, press, embedding=rotated(0))
    later_official = signal(s, official, embedding=rotated(5), days=2)
    later_community = signal(s, community, embedding=rotated(3), days=3)

    counts = dedup_signals(s, ctx())

    ids = _cluster(s, early_press, later_official, later_community)
    assert ids == [later_official.id] * 3
    assert counts["canonical_changed"] == 1

    # Equal trust: the earliest wins.
    other = source(s, "Press 2", trust=0.7)
    first = signal(s, press, embedding=vec(1.0, axis=7, toward=8), days=1)
    second = signal(s, other, embedding=vec(0.99, axis=7, toward=8), days=2)
    dedup_signals(s, ctx())
    assert _cluster(s, first, second) == [first.id, first.id]


def test_canonical_lists_sources_and_duplicates_point_at_it(db_session):
    s = db_session
    a, b, c = source(s, "A", trust=0.9), source(s, "B"), source(s, "C")
    x = signal(s, a, embedding=rotated(0))
    y = signal(s, b, embedding=rotated(10), days=1)
    z = signal(s, c, embedding=rotated(12), days=1)
    dedup_signals(s, ctx())
    s.expire_all()
    head = s.get(Signal, x.id).meta["dedup"]
    assert head["members"] == sorted([x.id, y.id, z.id])
    assert head["source_count"] == 3 and head["source_ids"] == sorted([a.id, b.id, c.id])
    dup = s.get(Signal, y.id).meta["dedup"]
    assert dup["canonical_id"] == x.id and dup["similarity"] == pytest.approx(0.985, abs=1e-3)
    # Duplicates are kept, not deleted.
    assert len(s.scalars(select(Signal)).all()) == 3


def test_rerun_changes_nothing(db_session):
    s = db_session
    a, b = source(s, "A"), source(s, "B")
    sigs = [signal(s, a, embedding=rotated(0)), signal(s, b, embedding=rotated(10), days=1)]
    sigs.append(signal(s, b, embedding=vec(1.0, axis=9), days=2))
    dedup_signals(s, ctx())
    before = [(x.dedup_cluster_id, dict(x.meta)) for x in s.scalars(select(Signal)).all()]

    counts = dedup_signals(s, ctx())

    s.expire_all()
    after = [(x.dedup_cluster_id, dict(x.meta)) for x in s.scalars(select(Signal)).all()]
    assert after == before
    assert counts["pending"] == 0 and counts["meta_refreshed"] == 0


def test_new_signal_joins_existing_cluster_on_a_later_run(db_session):
    s = db_session
    a, b = source(s, "A", trust=0.9), source(s, "B")
    x = signal(s, a, embedding=rotated(0))
    dedup_signals(s, ctx())
    y = signal(s, b, embedding=rotated(10), days=1)
    dedup_signals(s, ctx())
    assert _cluster(s, x, y) == [x.id, x.id]
    assert s.get(Signal, x.id).meta["dedup"]["members"] == sorted([x.id, y.id])


def test_time_window(db_session):
    s = db_session
    a, b = source(s, "A"), source(s, "B")
    x = signal(s, a, embedding=rotated(0))
    far = signal(s, b, embedding=rotated(0), days=15)  # identical, but 15 days apart
    near = signal(s, b, embedding=rotated(0), days=-13)
    dedup_signals(s, ctx(window_days=14))
    # near is processed first (oldest), x joins it; far is outside both windows.
    assert _cluster(s, near, x, far) == [near.id, near.id, far.id]


def test_only_in_scope_and_current_model(db_session):
    s = db_session
    a, b = source(s, "A"), source(s, "B")
    x = signal(s, a, embedding=rotated(0))
    old_model = signal(s, b, embedding=rotated(1), model="old/model")
    inactive_src = source(s, "Inactive")
    hidden = signal(s, inactive_src, embedding=rotated(2))
    s.execute(update(Source).where(Source.id == inactive_src.id).values(is_active=False))
    dedup_signals(s, ctx())
    assert _cluster(s, x, old_model, hidden) == [x.id, None, None]
