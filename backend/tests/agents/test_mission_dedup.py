"""Approving a finding links it to a signal that already tells the story instead of duplicating it,
and mission confidence bands come from config/pipeline.yaml (D82)."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.mission import service
from app.agents.mission.service import approve_finding, reject_finding
from app.core.catalog import MissionSettings, load_pipeline
from app.db.enums import MissionSourceKind, ReviewStatus, Verification
from app.db.models import Mission, MissionFinding, MissionSource, Signal
from tests.agents.conftest import NOW, make_mission
from tests.pipeline.factories import MODEL, FakeEmbed, vec

PAGE = "https://help.sonatype.com/en/reachability-analysis.html"
STATEMENT = "Sonatype Lifecycle offers reachability analysis for Java applications."


def _signal(s: Session, n: int, url: str, **kw: Any) -> Signal:
    """A visible (enriched, canonical, in scope) signal."""
    args: dict[str, Any] = {
        "url": url, "canonical_url": url, "content_hash": f"{n:064d}", "title": f"Signal {n}",
        "raw_text": "text", "published_at": NOW - timedelta(days=2), "fetched_at": NOW,
        "relevant": True, "summary": "A summary.", "impact": 4, "confidence": 0.8,
        "verification": Verification.SUPPORTED, "enriched_at": NOW, "meta": {},
    }  # fmt: skip
    args.update(kw)
    sig = Signal(**args)
    s.add(sig)
    s.flush()
    return sig


def _finding(s: Session, m: Mission, url: str = PAGE, statement: str = STATEMENT,
             confidence: str = "high") -> MissionFinding:  # fmt: skip
    src = s.scalar(select(MissionSource).where(MissionSource.mission_id == m.id,
                                               MissionSource.canonical_url == url))  # fmt: skip
    if src is None:  # one source row per page and mission; a page can yield several findings
        src = MissionSource(mission_id=m.id, kind=MissionSourceKind.WEB, url=url,
                            canonical_url=url, title="Reachability analysis",
                            excerpt="Page excerpt.", fetched_at=NOW)  # fmt: skip
        s.add(src)
        s.flush()
    f = MissionFinding(mission_id=m.id, sub_question_no=1, statement=statement,
                       evidence_quote="Reachability analysis for Java.", source_id=src.id,
                       confidence=confidence, verification="supported", impact=4)  # fmt: skip
    s.add(f)
    s.flush()
    return f


def _count(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(Signal)) or 0


def test_same_page_as_a_collected_signal_links_it(s: Session, cfg: MissionSettings):
    m = make_mission(s, cfg)
    collected = _signal(s, 1, PAGE)
    f = _finding(s, m)
    before = _count(s)

    assert approve_finding(s, f, "Dana", NOW, embed=None) == collected.id
    assert _count(s) == before and f.signal_id == collected.id

    # Rejecting the finding afterwards leaves the collected signal alone.
    reject_finding(s, f, "Dana", NOW, None, None)
    s.refresh(collected)
    assert collected.status != ReviewStatus.REJECTED


def test_similar_story_from_another_url_links_it(s: Session, cfg: MissionSettings):
    m = make_mission(s, cfg)
    threshold = load_pipeline().dedup.cross_source_threshold
    blog = _signal(s, 1, "https://www.sonatype.com/blog/reachability",
                   embedding=vec(1.0), embedding_model=MODEL)  # fmt: skip
    f = _finding(s, m)
    embed = FakeEmbed({STATEMENT: vec(threshold + 0.01)})

    assert approve_finding(s, f, "Dana", NOW, embed) == blog.id
    assert len(embed.calls) == 1


def test_below_threshold_creates_a_new_signal_with_the_embedding(s: Session, cfg: MissionSettings):
    m = make_mission(s, cfg)
    threshold = load_pipeline().dedup.cross_source_threshold
    other = _signal(s, 1, "https://www.sonatype.com/blog/other",
                    embedding=vec(1.0), embedding_model=MODEL)  # fmt: skip
    f = _finding(s, m)

    sig_id = approve_finding(s, f, "Dana", NOW, FakeEmbed({STATEMENT: vec(threshold - 0.05)}))

    assert sig_id != other.id
    sig = s.get(Signal, sig_id)
    assert sig.meta["kind"] == "mission" and sig.meta["finding_id"] == f.id
    assert sig.embedding is not None and sig.embedding_model == MODEL


def test_findings_of_one_page_stay_separate_and_hidden_signals_never_match(
    s: Session, cfg: MissionSettings
):
    m = make_mission(s, cfg)
    _signal(s, 1, PAGE, status=ReviewStatus.REJECTED)  # rejected: not visible, never a match
    first = approve_finding(s, _finding(s, m), "Dana", NOW, embed=None)
    second_f = _finding(s, m, statement="Reachability analysis is not supported for containers.")

    second = approve_finding(s, second_f, "Dana", NOW, embed=None)

    assert first != second  # another finding of the same page is not the same story
    assert {s.get(Signal, i).meta["kind"] for i in (first, second)} == {"mission"}


def test_rejecting_a_finding_linked_to_another_findings_signal_keeps_that_signal(
    s: Session, cfg: MissionSettings
):
    m = make_mission(s, cfg)
    a = _finding(s, m, url="https://a.test/1")
    b = _finding(s, m, url="https://b.test/2", statement="The same story, told again here.")
    embed = FakeEmbed({STATEMENT: vec(1.0), "The same story": vec(1.0)})
    a_sig = approve_finding(s, a, "Dana", NOW, embed)
    assert approve_finding(s, b, "Dana", NOW, embed) == a_sig  # same mission, same story

    reject_finding(s, b, "Dana", NOW, None, None)

    assert s.get(Signal, a_sig).status == ReviewStatus.APPROVED


@pytest.mark.parametrize(
    ("confidence", "verification", "bands", "expected"),
    [
        ("high", "supported", None, "high"),  # 0.85
        ("medium", "supported", None, "medium"),  # 0.65
        ("low", "supported", None, "low"),  # 0.45
        ("high", "partial", None, "medium"),  # capped at 0.65
        ("high", "supported", {"high": 0.9, "medium": 0.5}, "medium"),  # bands come from config
        ("low", "supported", {"high": 0.9, "medium": 0.4}, "medium"),
    ],
)
def test_confidence_band_follows_config(s: Session, cfg: MissionSettings, monkeypatch,
                                        confidence, verification, bands, expected):  # fmt: skip
    if bands is not None:
        real = load_pipeline()
        score = real.score.model_copy(update={"bands": real.score.bands.model_copy(update=bands)})
        patched = real.model_copy(update={"score": score})
        monkeypatch.setattr(service, "load_pipeline", lambda: patched)
    m = make_mission(s, cfg)
    f = _finding(s, m, confidence=confidence)
    f.verification = verification

    sig = s.get(Signal, approve_finding(s, f, "Dana", NOW, embed=None))

    assert sig.meta["score"]["band"] == expected
    assert sig.meta["score"]["confidence"] == sig.confidence
