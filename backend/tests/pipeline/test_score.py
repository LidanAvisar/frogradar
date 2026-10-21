"""Confidence formula (code, no LLM): worked examples, caps, corroboration, bands, the step."""

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.core.catalog import ScoreBands, ScoreSettings, ScoreWeights
from app.db.enums import Verification
from app.db.models import Signal
from app.pipeline.score import compute, score_signals
from tests.pipeline.factories import ctx, signal, source

CFG = ScoreSettings()
V = Verification


def _c(verification=V.SUPPORTED, trust=0.9, n=1, quote=True):
    return compute(
        CFG, verification=verification, trust=trust, sources_in_cluster=n, quote_verified=quote
    )


def test_worked_examples():
    # Official blog, supported, single source, quote ok: 0.45 + 0.225 + 0 + 0.15
    s = _c()
    assert s.confidence == 0.825 and s.band == "high" and s.caps_applied == []
    # Press, partial: 0.225 + 0.175 + 0 + 0.15
    assert _c(V.PARTIAL, trust=0.7).confidence == 0.55
    assert _c(V.PARTIAL, trust=0.7).band == "medium"
    # No source → no_source_trust (0.5)
    assert _c(trust=None).components["trust"] == 0.5


def test_corroboration():
    assert _c(n=1).components["corroboration"] == 0
    assert _c(n=2).components["corroboration"] == 0.5
    assert _c(n=3).components["corroboration"] == 1.0
    assert _c(n=7).components["corroboration"] == 1.0
    assert _c(n=3).confidence == 0.975


def test_caps():
    unsupported = _c(V.UNSUPPORTED, n=3)  # 0 + 0.225 + 0.15 + 0.15 = 0.525 → capped
    assert unsupported.confidence == 0.30 and unsupported.caps_applied == ["unsupported"]
    assert unsupported.band == "low"
    no_quote = _c(n=3, quote=False)  # 0.825 → capped
    assert no_quote.confidence == 0.60 and no_quote.caps_applied == ["quote_unverified"]


def test_pending_has_no_confidence():
    with pytest.raises(ValueError):
        _c(V.PENDING)


def test_config_validation():
    with pytest.raises(ValidationError, match="sum to 1"):
        ScoreWeights(verification=0.5, trust=0.5, corroboration=0.5, evidence=0)
    with pytest.raises(ValidationError, match="medium must be below high"):
        ScoreBands(high=0.5, medium=0.6)


@pytest.mark.db
def test_score_step_writes_confidence_and_breakdown(db_session):
    s = db_session
    official, press = source(s, "Official", trust=0.9), source(s, "Press", trust=0.7)
    head = signal(s, official, "head")
    dup = signal(s, press, "dup")
    pending = signal(s, press, "pending")
    s.execute(
        update(Signal)
        .where(Signal.id.in_([head.id, dup.id]))
        .values(
            dedup_cluster_id=head.id,
            enriched_at=head.fetched_at,
            verification=V.SUPPORTED,
            evidence_quote_verified=True,
        )
    )
    s.execute(update(Signal).where(Signal.id == pending.id).values(enriched_at=head.fetched_at))

    counts = score_signals(s, ctx())
    s.expire_all()

    # Two sources in the cluster → corroboration 0.5 for both members.
    assert s.get(Signal, head.id).confidence == pytest.approx(0.45 + 0.225 + 0.075 + 0.15)
    assert s.get(Signal, dup.id).confidence == pytest.approx(0.45 + 0.175 + 0.075 + 0.15)
    meta = s.get(Signal, head.id).meta["score"]
    assert meta["band"] == "high" and meta["components"]["corroboration"] == 0.5
    assert s.get(Signal, pending.id).confidence is None
    assert counts == {
        "scored": 2, "changed": 2, "pending": 1, "high": 2, "medium": 0, "low": 0,
    }  # fmt: skip
    assert score_signals(s, ctx())["changed"] == 0  # deterministic
