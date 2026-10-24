"""GET /api/entities/{slug}/battlecard: card + matrix (JFrog from config vs competitor) + timeline +
coverage; read-time citation check; 404s."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.catalog import load_self_capabilities
from app.db.enums import Relationship, ReportKind
from app.db.models import Capability, CapabilityMatrix, Entity, Report
from tests.api.conftest import NOW


def _cap(s, slug, segment="supply_chain_security"):
    s.execute(
        pg_insert(Capability)
        .values(slug=slug, name=slug.title(), segment=segment, description=slug)
        .on_conflict_do_nothing(index_elements=["slug"])
    )
    return s.scalar(select(Capability.id).where(Capability.slug == slug))


def _world(build):
    s = build.s
    sonatype = build.entity(
        "sonatype", "Sonatype", relationship=Relationship.COMPETITOR,
        segments=["supply_chain_security"],
    )  # fmt: skip
    firewall = build.entity("sonatype-firewall", "Repository Firewall", parent=sonatype)
    other = build.entity("github", "GitHub", relationship=Relationship.COMPETITOR)
    fw = build.signal("Firewall 2", impact=4, hours_ago=5)
    price = build.signal("Pricing page changed", impact=3, hours_ago=30,
                         meta={"display_title": "Sonatype raises Nexus Cloud Pro"})  # fmt: skip
    gone = build.signal("Pending re-enrichment", enriched=False)
    gh = build.signal("GitHub only")
    build.link(fw, firewall)
    build.link(price, sonatype)
    build.link(gone, sonatype)
    build.link(gh, other)
    curation, sbom = _cap(s, "package-curation"), _cap(s, "sbom")
    _cap(s, "model-registry", segment="mlops")
    s.add_all([
        CapabilityMatrix(entity_id=sonatype.id, capability_id=curation, level="full",
                         previous_level="partial", level_changed_at=NOW - timedelta(days=2),
                         evidence_signal_id=fw.id, notes="Firewall 2 blocks packages.",
                         last_verified_at=NOW),
        CapabilityMatrix(entity_id=sonatype.id, capability_id=sbom, level="partial",
                         evidence_signal_id=gone.id, notes="evidence no longer usable"),
    ])  # fmt: skip
    s.add(Report(
        kind=ReportKind.BATTLECARD, entity_id=sonatype.id, model="m", created_at=NOW,
        content={
            "positioning": {"text": "Sonatype sells a safe registry.", "signal_ids": [fw.id]},
            "strengths": [
                {"text": "Firewall 2 blocks malware.", "signal_ids": [fw.id]},
                {"text": "Relies on a hidden signal.", "signal_ids": [fw.id, gone.id]},
            ],
            "weaknesses": [{"text": "Cloud Pro got pricier.", "signal_ids": [price.id]}],
            "opportunities_for_jfrog": [], "blind_spots_for_jfrog": [],
        },
    ))  # fmt: skip
    s.flush()
    return fw, price, gone, gh


def test_battlecard_shape(client, build):
    fw, price, gone, gh = _world(build)
    data = client.get("/api/entities/sonatype/battlecard").json()

    assert data["entity"] == {
        "slug": "sonatype", "name": "Sonatype", "relationship": "competitor",
        "segments": ["supply_chain_security"],
    }  # fmt: skip
    assert data["positioning"]["signal_ids"] == [fw.id]
    assert [i["text"] for i in data["strengths"]] == [
        "Firewall 2 blocks malware."
    ]  # read-time drop
    assert {c["id"] for c in data["citations"]} == {fw.id, price.id}

    rows = {r["capability"]["slug"]: r for r in data["matrix"]}
    assert set(rows) == {"package-curation", "sbom"}  # competitor's segment only
    cur = rows["package-curation"]
    assert cur["competitor"]["level"] == "full" and cur["changed_recently"] is True
    assert cur["competitor"]["evidence"]["url"] == fw.url
    jfrog = load_self_capabilities().capabilities["package-curation"]
    assert cur["jfrog"] == {"level": jfrog.level, "note": jfrog.note, "source": jfrog.source}
    assert rows["sbom"]["competitor"]["level"] == "unknown"  # evidence not usable any more

    assert [t["id"] for t in data["timeline"]] == [fw.id, price.id]
    assert data["timeline"][1]["title"] == "Sonatype raises Nexus Cloud Pro"
    cov = data["coverage"]
    assert (cov["signals"], cov["high_impact"], cov["capabilities_known"]) == (2, 1, 1)
    assert cov["capabilities_total"] == 2


def test_timeline_marks_research_findings(client, build):
    _world(build)
    sonatype = build.s.scalar(select(Entity).where(Entity.slug == "sonatype"))
    finding = build.signal("Docs describe reachability", impact=4, published=False,
                           fetched_hours_ago=1, meta={"kind": "mission"})  # fmt: skip
    build.link(finding, sonatype)
    timeline = client.get("/api/entities/sonatype/battlecard").json()["timeline"]
    assert timeline[0]["id"] == finding.id and timeline[0]["research"] is True
    assert not any(t["research"] for t in timeline[1:])


def test_no_card_yet_still_returns_matrix_and_timeline(client, build):
    build.entity("snyk", "Snyk", relationship=Relationship.COMPETITOR)
    data = client.get("/api/entities/snyk/battlecard").json()
    assert data["report_id"] is None and data["positioning"] is None and data["strengths"] == []
    assert data["timeline"] == [] and data["coverage"]["signals"] == 0


def test_unknown_self_and_product_slugs_404(client, build):
    jfrog = build.entity("jfrog", "JFrog", relationship=Relationship.SELF, is_self=True)
    build.entity("jfrog-xray", "JFrog Xray", parent=jfrog)
    for slug in ("nope", "jfrog", "jfrog-xray"):
        assert client.get(f"/api/entities/{slug}/battlecard").status_code == 404


def test_battlecard_list_cards_first_then_no_card_yet(client, build):
    _world(build)  # Sonatype has a card (2 signals), GitHub none (1 signal)
    build.entity("snyk", "Snyk", relationship=Relationship.COMPETITOR)
    build.entity("jfrog", "JFrog", relationship=Relationship.SELF, is_self=True)
    data = client.get("/api/battlecards").json()
    assert [(i["slug"], i["generated_at"] is not None) for i in data] == [
        ("sonatype", True), ("github", False), ("snyk", False),
    ]  # fmt: skip
    assert data[0]["signal_count"] == 2 and data[1]["signal_count"] == 1


# ---------- enough evidence for a card (D86) ----------


def test_card_below_the_threshold_is_withheld_but_matrix_and_timeline_stay(
    client, build, card_threshold
):
    fw, price, _, _ = _world(build)  # Sonatype: 2 usable signals about it (one via its product)
    card_threshold(3)
    data = client.get("/api/entities/sonatype/battlecard").json()
    assert data["evidence"] == {
        "signals_about": 2,
        "required": 3,
        "lookback_days": 180,
        "enough": False,
    }
    assert data["report_id"] is None and data["generated_at"] is None
    assert data["positioning"] is None and data["strengths"] == [] and data["citations"] == []
    rows = {r["capability"]["slug"]: r for r in data["matrix"]}
    assert rows["package-curation"]["competitor"]["level"] == "full"  # its own evidence link
    assert {t["id"] for t in data["timeline"]} == {fw.id, price.id}

    listed = {i["slug"]: i for i in client.get("/api/battlecards").json()}
    assert listed["sonatype"]["generated_at"] is None
    assert listed["sonatype"]["evidence"]["enough"] is False


def test_card_at_the_threshold_is_shown(client, build, card_threshold):
    fw, _, _, _ = _world(build)
    card_threshold(2)
    data = client.get("/api/entities/sonatype/battlecard").json()
    assert data["evidence"]["enough"] is True and data["report_id"] is not None
    assert data["positioning"]["signal_ids"] == [fw.id]


def test_mentions_old_and_unusable_signals_do_not_count(client, build, card_threshold):
    _world(build)
    sonatype = build.s.scalar(select(Entity).where(Entity.slug == "sonatype"))
    mention = build.signal("GitHub mentions Sonatype")
    build.link(mention, sonatype, primary=False)
    old = build.signal("Sonatype in March", hours_ago=24 * 200)  # outside the 180-day lookback
    build.link(old, sonatype)
    card_threshold(3)
    data = client.get("/api/entities/sonatype/battlecard").json()
    assert data["evidence"]["signals_about"] == 2 and data["report_id"] is None
