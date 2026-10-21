"""Calibration report: buckets and sections, on synthetic vectors (no LLM)."""

import io

import pytest

from app.pipeline.dedup import dedup_signals
from app.pipeline.link import link_signals
from app.pipeline.report import buckets, print_report
from tests.pipeline.factories import ctx, entity, rotated, signal, source


def test_buckets():
    out = dict(buckets([0.1, 0.52, 0.86, 0.99, 1.0]))
    assert out["< 0.50"] == 1 and out["0.50–0.55"] == 1
    assert out["0.85–0.90"] == 1 and out["0.95–1.00"] == 2
    assert sum(out.values()) == 5


@pytest.mark.db
def test_report_sections(db_session):
    s = db_session
    snyk = entity(s, "snyk", "Snyk")
    a, b = source(s, "Alpha"), source(s, "Beta", entity=snyk)
    signal(s, a, "Snyk acquires X", text="Snyk", embedding=rotated(0))
    signal(s, b, "Snyk buys X", text="Snyk", embedding=rotated(10), days=1)
    signal(s, a, "Other news", embedding=rotated(60), days=2)
    c = ctx()
    dedup_signals(s, c)
    link_signals(s, c)
    out = io.StringIO()

    print_report(s, c, out)

    text = out.getvalue()
    assert "Top 20 cross-source pairs" in text and "Top 10 same-source pairs" in text
    assert " 0.985  yes" in text  # the merged cross-source pair
    assert "Nearest cross-source neighbour" in text and "← cross-source threshold" in text
    assert "snyk" in text and "Top 10 signals by entity links" in text
