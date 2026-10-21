"""Gold labels from analyst reviews (S14, D70): make eval-gold."""

import pytest
from sqlalchemy import text

from app.db.enums import SignalCategory
from tests.api.conftest import RAW_SENTINEL

pytestmark = pytest.mark.db


@pytest.fixture
def clean(db_session, client):
    db_session.execute(text("TRUNCATE feedback RESTART IDENTITY"))
    return client


def test_gold_export(clean, build, db_session, tmp_path):
    from app.eval.files import read_labels
    from app.eval.gold import export

    ent = build.entity("sonatype", "Sonatype")
    ok = build.signal("approved", impact=3, category=SignalCategory.PRICING_CHANGE)
    build.link(ok, ent)
    fixed = build.signal("corrected", impact=4)
    gone = build.signal("not relevant")
    dup = build.signal("duplicate")
    build.signal("untouched")
    clean.post(f"/api/signals/{ok.id}/approve", json={"analyst": "dana"})
    clean.post(
        f"/api/signals/{fixed.id}/correct",
        json={"analyst": "lee", "reason_code": "impact_too_high", "comment": "minor", "impact": 2},
    )
    clean.post(f"/api/signals/{gone.id}/reject", json={"reason_code": "not_relevant"})
    clean.post(f"/api/signals/{dup.id}/reject", json={"reason_code": "duplicate"})

    path, rows = export(db_session, tmp_path / "gold.csv")
    back = {r.id: r for r in read_labels(path)}
    assert set(back) == {ok.id, fixed.id, gone.id}  # duplicate rejection and untouched left out
    a, c, r = back[ok.id], back[fixed.id], back[gone.id]
    assert (a.relevant, a.category, a.impact, a.primary_entity) == (
        "y",
        "pricing_change",
        "3",
        "sonatype",
    )
    assert (a.label_source, a.stratum) == ("analyst:dana approve", "gold:approve")
    assert (c.impact, c.label_source, c.label_reason) == (
        "2",
        "analyst:lee edit",
        "impact_too_high; minor",
    )
    assert (r.relevant, r.category, r.impact) == ("n", "", "")
    assert RAW_SENTINEL not in path.read_text()
