"""Ask retrieval on the test DB: hybrid ranking, filters per document type, usable-only, caps."""

from datetime import timedelta

import pytest

from app.core.catalog import AskSettings
from app.db.enums import (
    ProfileStatus,
    RadarKind,
    RadarStatus,
    Relationship,
    ReportKind,
    Verification,
)
from app.db.models import RadarCandidate, Report
from app.rag.filters import infer_filters
from app.rag.retrieve import retrieve
from tests.api.conftest import NOW
from tests.pipeline.factories import FakeEmbed, vec

pytestmark = pytest.mark.db

Q = "Who launched a package firewall?"


def _world(build, s):
    sonatype = build.entity(
        "sonatype", "Sonatype", relationship=Relationship.COMPETITOR, aliases=["Sonatype Inc"]
    )
    nexus = build.entity("sonatype-nexus", "Nexus Repository", parent=sonatype)
    github = build.entity("github", "GitHub", relationship=Relationship.COMPETITOR)
    build.entity("jfrog", "JFrog", relationship=Relationship.SELF, is_self=True)
    near = build.signal(
        "GitHub package firewall in public preview",
        embedding=vec(0.9),
        summary="GitHub launched a package firewall.",
    )
    text_only = build.signal(
        "Nexus blocks malicious packages at entry",
        embedding=vec(1.0, axis=7),
        summary="Sonatype adds a firewall to Nexus Repository Pro+.",
        hours_ago=24 * 40,
    )
    unrelated = build.signal(
        "Xcode runner image available", embedding=vec(1.0, axis=9), summary="A new macOS runner."
    )
    unsupported = build.signal(
        "Firewall rumour",
        embedding=vec(0.95),
        verification=Verification.UNSUPPORTED,
        summary="Package firewall.",
    )
    irrelevant = build.signal(
        "Firewall gossip", embedding=vec(0.95), relevant=False, summary="Package firewall."
    )
    build.link(near, github)
    build.link(text_only, nexus)
    s.add(
        Report(
            kind=ReportKind.BATTLECARD,
            entity_id=sonatype.id,
            model="m",
            created_at=NOW,
            content={
                "weaknesses": [
                    {"text": "Firewall only in Pro+ editions.", "signal_ids": [text_only.id]}
                ],
                "strengths": [{"text": "Relies on a hidden one.", "signal_ids": [unsupported.id]}],
            },
        )
    )
    s.add(
        RadarCandidate(
            name="Regshield",
            key="regshield",
            kind=RadarKind.OPEN_SOURCE_PROJECT,
            first_seen=NOW - timedelta(days=3),
            last_seen=NOW,
            status=RadarStatus.NEW,
            profile_status=ProfileStatus.OK,
            mention_signal_ids=[],
            source_ids=[],
            segments=[],
            metrics={"relation": "threatens"},
            meta={},
            merges=[],
            profile={
                "items": {
                    "what_it_is": {
                        "statement": "An open-source package firewall for registries.",
                        "signal_ids": [1],
                    }
                }
            },
        )
    )
    s.flush()
    return {
        "near": near,
        "text_only": text_only,
        "unrelated": unrelated,
        "unsupported": unsupported,
        "irrelevant": irrelevant,
    }


def _embed():
    return FakeEmbed({Q: vec(1.0), "Sonatype battlecard": vec(1.0, axis=3), "Regshield": vec(0.8)})


def _ids(found):
    return [h.doc.id for h in found.hits]


def test_hybrid_ranking_uses_vectors_and_full_text(client, build, db_session):
    w = _world(build, db_session)
    found = retrieve(
        db_session, Q, infer_filters(db_session, Q, NOW), embed=_embed(), cfg=AskSettings()
    )
    ids = _ids(found)
    assert ids[0] == f"sig:{w['near'].id}"  # in both lists
    assert f"sig:{w['text_only'].id}" in ids  # full text only ("firewall", "package")
    assert "radar:" + str(db_session.query(RadarCandidate).one().id) in ids  # vector + text
    assert f"sig:{w['unrelated'].id}" not in ids  # cosine 0 < min_cosine, no text match
    near = found.hits[0]
    assert (
        near.vector_rank == 1 and near.text_rank is not None and near.cosine == pytest.approx(0.9)
    )


def test_only_usable_signals_and_checked_battlecard_items(client, build, db_session):
    w = _world(build, db_session)
    found = retrieve(
        db_session, Q, infer_filters(db_session, Q, NOW), embed=_embed(), cfg=AskSettings(k=12)
    )
    ids = _ids(found)
    assert f"sig:{w['unsupported'].id}" not in ids and f"sig:{w['irrelevant'].id}" not in ids
    bc = [h.doc for h in found.hits if h.doc.type == "battlecard"]
    assert [d.id for d in bc] == [
        "bc:sonatype:weaknesses:1"
    ]  # the item citing an unsupported signal is out
    assert bc[0].url == "/battlecards/sonatype" and bc[0].cites == (w["text_only"].id,)


def test_company_filter_applies_to_every_document_type(client, build, db_session):
    w = _world(build, db_session)
    q = "What did Nexus Repository change about the package firewall?"
    f = infer_filters(db_session, q, NOW)
    assert (f.company_slug, f.window) == ("sonatype", None)  # product → its company
    ids = _ids(retrieve(db_session, q, f, embed=_embed(), cfg=AskSettings()))
    assert f"sig:{w['near'].id}" not in ids  # GitHub's signal
    assert f"sig:{w['text_only'].id}" in ids
    assert "bc:sonatype:weaknesses:1" in ids
    assert not [i for i in ids if i.startswith("radar:")]  # radar players aren't tracked companies


def test_time_filter_drops_old_signals_and_undated_battlecard_items(client, build, db_session):
    w = _world(build, db_session)
    q = "Package firewall news this month"
    f = infer_filters(db_session, q, NOW)
    assert f.window == "last 30 days" and f.company_slug is None
    ids = _ids(retrieve(db_session, q, f, embed=_embed(), cfg=AskSettings()))
    assert f"sig:{w['text_only'].id}" not in ids  # 40 days old
    assert not [i for i in ids if i.startswith("bc:")]
    assert [i for i in ids if i.startswith("radar:")]  # first seen 3 days ago


def test_company_inference_edge_cases(client, build, db_session):
    _world(build, db_session)
    assert infer_filters(db_session, "GitHub vs Sonatype pricing", NOW).company_slug is None
    assert infer_filters(db_session, "What is JFrog doing?", NOW).company_slug is None
    assert infer_filters(db_session, "news from sonatype inc", NOW).company_slug == "sonatype"
    assert infer_filters(db_session, "githubbers", NOW).company_slug is None  # word boundary
    f = infer_filters(db_session, "GitHub this week", NOW)
    assert f.company_slug == "github" and f.since == NOW - timedelta(days=7)


def test_k_and_max_docs_cap_the_result(client, build, db_session):
    for i in range(15):
        build.signal(f"Package firewall note {i}", embedding=vec(0.8), summary="package firewall")
    found = retrieve(
        db_session,
        Q,
        infer_filters(db_session, Q, NOW),
        embed=_embed(),
        cfg=AskSettings(k=12, max_docs=12),
    )
    assert len(found.hits) == 12
    assert found.candidates == 15
    small = retrieve(
        db_session, Q, infer_filters(db_session, Q, NOW), embed=_embed(), cfg=AskSettings(k=3)
    )
    assert len(small.hits) == 3


def test_raw_text_is_never_in_documents(client, build, db_session):
    from tests.api.conftest import RAW_SENTINEL

    _world(build, db_session)
    found = retrieve(
        db_session, Q, infer_filters(db_session, Q, NOW), embed=_embed(), cfg=AskSettings(k=12)
    )
    assert found.hits and all(RAW_SENTINEL not in h.doc.text for h in found.hits)
