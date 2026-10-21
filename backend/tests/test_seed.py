import pytest
import yaml
from sqlalchemy import func, select, text, update

from app.core import catalog
from app.db.checks import EmbeddingDimMismatch, assert_embedding_dim_matches_db
from app.db.enums import DiscoveredBy, EntityReviewStatus, EntityType, FetchMethod, SourceKind
from app.db.models import Capability, Entity, Source
from app.db.seed import seed

pytestmark = pytest.mark.db


def _counts(session) -> dict[str, int]:
    return {
        m.__tablename__: session.scalar(select(func.count()).select_from(m))
        for m in (Entity, Source, Capability)
    }


def _edit_yaml(path, mutate) -> None:
    data = yaml.safe_load(path.read_text())
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_seed_is_idempotent(db_session):
    first = seed(db_session)
    counts = _counts(db_session)
    second = seed(db_session)

    assert _counts(db_session) == counts
    competitors = catalog.load_competitors()
    n_entities = sum(1 + len(c.products) for c in competitors.companies)
    assert counts["entities"] == n_entities == first.tables["entities"].inserted
    assert counts["sources"] == (
        len(competitors.all_sources())
        + len(catalog.load_sources().sources)
        + len(catalog.load_discovery().sources)
    )
    for c in second.tables.values():
        assert (c.inserted, c.updated, c.deactivated) == (0, 0, 0)


def test_seeded_entities_shape(db_session):
    seed(db_session)
    rows = {e.slug: e for e in db_session.scalars(select(Entity))}

    assert [s for s, e in rows.items() if e.is_self] == ["jfrog"]
    assert rows["jfrog-xray"].parent_id == rows["jfrog"].id
    assert rows["aws-codeartifact"].parent_id == rows["aws"].id
    assert rows["github"].relationship == "coopetition"
    assert rows["aws"].aliases == []
    assert rows["github-packages"].aliases == ["GitHub Container Registry", "GHCR"]
    assert rows["socket"].match_rules["Socket"]["case_sensitive"] is True
    assert rows["snyk"].match_rules == {}
    assert all(e.review_status == EntityReviewStatus.APPROVED for e in rows.values())
    # Product-level sources hang off the product, not the company.
    pricing = db_session.scalar(select(Source).where(Source.name == "AWS CodeArtifact Pricing"))
    assert pricing.entity_id == rows["aws-codeartifact"].id


def test_config_change_updates_in_place(db_session, config_copy):
    seed(db_session, config_copy)
    before = db_session.scalar(select(Entity).where(Entity.slug == "snyk"))
    snyk_id = before.id

    def rename(data):
        next(c for c in data["companies"] if c["slug"] == "snyk")["name"] = "Snyk Renamed"

    _edit_yaml(config_copy / "competitors.yaml", rename)
    catalog.clear_cache()
    report = seed(db_session, config_copy)
    db_session.expire_all()

    assert report.tables["entities"].updated == 1
    assert report.tables["entities"].inserted == 0
    after = db_session.scalar(select(Entity).where(Entity.slug == "snyk"))
    assert (after.id, after.name) == (snyk_id, "Snyk Renamed")


def test_removed_source_is_deactivated_not_deleted(db_session, config_copy):
    seed(db_session, config_copy)
    # Remove an active source (most are inactive in the demo config).
    sources = yaml.safe_load((config_copy / "sources.yaml").read_text())["sources"]
    idx = next(i for i, src in enumerate(sources) if src.get("is_active", True))
    removed_url = sources[idx]["url"]
    _edit_yaml(config_copy / "sources.yaml", lambda d: d["sources"].pop(idx))
    catalog.clear_cache()
    report = seed(db_session, config_copy)
    db_session.expire_all()

    assert report.tables["sources"].deactivated == 1
    source = db_session.scalar(select(Source).where(Source.url == removed_url))
    assert source is not None and source.is_active is False


def test_seed_keeps_analyst_owned_fields(db_session):
    seed(db_session)
    db_session.execute(
        update(Entity)
        .where(Entity.slug == "socket")
        .values(is_tracked=False, review_status=EntityReviewStatus.REJECTED)
    )
    seed(db_session)
    db_session.expire_all()
    socket = db_session.scalar(select(Entity).where(Entity.slug == "socket"))
    assert socket.is_tracked is False
    assert socket.review_status == EntityReviewStatus.REJECTED


def test_url_in_both_source_files_is_rejected(db_session, config_copy):
    first_competitor_url = catalog.load_competitors(config_copy).all_sources()[0].url
    _edit_yaml(
        config_copy / "sources.yaml",
        lambda d: d["sources"].append(
            {"name": "dupe", "url": first_competitor_url, "kind": "press", "fetch_method": "rss"}
        ),
    )
    catalog.clear_cache()
    with pytest.raises(catalog.ConfigError, match="both"):
        seed(db_session, config_copy)


def test_embedding_dim_check(db_session, config_copy):
    conn = db_session.connection()
    assert_embedding_dim_matches_db(conn)  # real config matches the migration

    _edit_yaml(config_copy / "llm_routing.yaml", lambda d: d["embedding"].update(dimensions=768))
    with pytest.raises(EmbeddingDimMismatch, match="768"):
        assert_embedding_dim_matches_db(conn, config_copy)


def test_embedding_dim_check_without_schema(make_database):
    from sqlalchemy import create_engine

    engine = create_engine(make_database())
    try:
        with engine.connect() as conn, pytest.raises(EmbeddingDimMismatch, match="migrate"):
            assert_embedding_dim_matches_db(conn)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()


def test_seed_writes_jfrog_capability_levels_from_config(db_session, config_copy):
    from app.db.models import CapabilityMatrix

    first = seed(db_session, config_copy)
    levels = catalog.load_self_capabilities(config_copy).capabilities
    rows = db_session.execute(
        select(Capability.slug, CapabilityMatrix)
        .join(CapabilityMatrix, CapabilityMatrix.capability_id == Capability.id)
        .join(Entity, Entity.id == CapabilityMatrix.entity_id)
        .where(Entity.is_self)
    ).all()
    assert first.tables["capability_matrix"].inserted == len(levels) == len(rows)
    by_slug = {slug: row for slug, row in rows}
    assert by_slug["package-curation"].level == levels["package-curation"].level
    assert levels["package-curation"].source in by_slug["package-curation"].notes
    assert all(r.evidence_signal_id is None for r in by_slug.values())

    # A changed level in config → updated with the previous level kept.
    _edit_yaml(
        config_copy / "jfrog_capabilities.yaml",
        lambda d: d["capabilities"]["ai-bom"].update(level="planned", source="https://x.test/a"),
    )
    catalog.clear_cache()
    second = seed(db_session, config_copy)
    assert second.tables["capability_matrix"].updated == 1
    db_session.refresh(by_slug["ai-bom"])
    assert (by_slug["ai-bom"].level, by_slug["ai-bom"].previous_level) == ("planned", "unknown")


def test_jfrog_capabilities_must_list_every_capability(config_copy):
    _edit_yaml(config_copy / "jfrog_capabilities.yaml", lambda d: d["capabilities"].pop("sbom"))
    catalog.clear_cache()
    with pytest.raises(catalog.ConfigError, match="missing slugs \\['sbom'\\]"):
        catalog.load_self_capabilities(config_copy)


def test_discontinued_products_are_listed_apart_in_prompts(db_session):
    from app.pipeline.prompting import Catalog

    seed(db_session)
    pipelines = db_session.scalar(select(Entity).where(Entity.slug == "jfrog-pipelines"))
    assert pipelines.discontinued_on is not None
    portfolio = Catalog.load(db_session).jfrog_portfolio
    active, _, ended = portfolio.partition("Discontinued JFrog products")
    assert "JFrog Pipelines" not in active and "JFrog Artifactory" in active
    assert "never recommend" in ended and "JFrog Pipelines (end of life 2026-05-01)" in ended


def test_discovery_sources_seeded(db_session):
    seed(db_session)
    methods = set(
        db_session.scalars(
            select(Source.fetch_method).where(
                Source.fetch_method.in_(["hn_search", "github_search", "cncf_landscape"])
            )
        )
    )
    assert methods == {"hn_search", "github_search", "cncf_landscape"}


def test_seed_never_touches_radar_rows(db_session):
    """Entities / sources an analyst approved from the radar survive a re-seed unchanged."""
    seed(db_session)
    entity = Entity(
        type=EntityType.COMPANY,
        name="NewCo",
        slug="newco",
        segments=["supply_chain_security"],
        review_status=EntityReviewStatus.APPROVED,
        discovered_by=DiscoveredBy.RADAR,
    )
    db_session.add(entity)
    db_session.flush()
    src = Source(
        entity_id=entity.id,
        name="NewCo releases",
        url="https://github.com/newco",
        kind=SourceKind.GITHUB,
        fetch_method=FetchMethod.GITHUB,
        trust_score=0.85,
        options={"repos": ["newco/tool"]},
        is_active=True,
        discovered_by=DiscoveredBy.RADAR,
    )
    db_session.add(src)
    db_session.flush()

    report = seed(db_session)
    db_session.expire_all()
    assert report.tables["sources"].deactivated == 0
    assert db_session.get(Source, src.id).is_active is True
    kept = db_session.get(Entity, entity.id)
    assert (kept.name, kept.discovered_by) == ("NewCo", DiscoveredBy.RADAR)
