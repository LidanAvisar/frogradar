from datetime import date

from app.db.enums import EntityType, Relationship, ReviewStatus, SignalCategory, Verification


def ids(client, **params):
    r = client.get("/api/signals", params=params)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def test_list_default_relevant_newest_first(client, build):
    old = build.signal("old", hours_ago=50)
    new = build.signal("new", hours_ago=1)
    irrelevant = build.signal("irrelevant", relevant=False)
    build.signal("raw", enriched=False)

    assert ids(client) == [new.id, old.id]
    assert ids(client, relevant=False) == [irrelevant.id]


def test_filter_by_entity_includes_products(client, build):
    company = build.entity("sonatype", "Sonatype")
    product = build.entity("sonatype-nexus", "Nexus Repository", parent=company)
    other = build.entity("snyk", "Snyk")
    a, b, c = build.signal("a"), build.signal("b"), build.signal("c")
    build.link(a, company)
    build.link(b, product)
    build.link(c, other)

    assert set(ids(client, entity="sonatype")) == {a.id, b.id}
    assert ids(client, entity="sonatype-nexus") == [b.id]
    assert ids(client, entity="unknown") == []


def test_filter_category_impact_dates(client, build):
    price = build.signal("price", category=SignalCategory.PRICING_CHANGE, impact=4, hours_ago=10)
    build.signal("feature", impact=2, hours_ago=100)

    assert ids(client, category="pricing_change") == [price.id]
    assert ids(client, min_impact=3) == [price.id]
    assert ids(client, since="2026-10-20T00:00:00Z") == [price.id]
    assert price.id not in ids(client, until="2026-10-20T00:00:00Z")
    assert client.get("/api/signals", params={"category": "nope"}).status_code == 422


def test_full_text_search(client, build):
    hit = build.signal(
        "Sonatype launches repository firewall", summary="Blocks malicious packages."
    )
    summary_hit = build.signal("Something else", summary="A new firewall for npm.")
    build.signal("GitHub Copilot update", summary="Faster completions.")

    assert set(ids(client, q="firewall")) == {hit.id, summary_hit.id}
    assert ids(client, q="malicious packages") == [hit.id]
    assert ids(client, q="nonexistentword") == []


def test_pagination(client, build):
    made = [build.signal(f"s{i}", hours_ago=i + 1) for i in range(5)]
    r = client.get("/api/signals", params={"page": 2, "page_size": 2}).json()
    assert r["total"] == 5 and r["page"] == 2 and r["page_size"] == 2
    assert [i["id"] for i in r["items"]] == [made[2].id, made[3].id]


def test_signal_detail_dto(client, build):
    company = build.entity("sonatype", "Sonatype")
    product = build.entity("sonatype-nexus", "Nexus Repository", parent=company)
    sig = build.signal(
        "Price change", is_claim=True, verification=Verification.PARTIAL, confidence=0.6,
        meta={"score": {"band": "medium"}, "dedup": {"source_count": 3},
              "quote_side": "before", "quote_section": "Plans > Pro"},
    )  # fmt: skip
    build.link(sig, product, primary=False)
    build.link(sig, company, primary=True)

    d = client.get(f"/api/signals/{sig.id}").json()

    assert d["is_claim"] is True
    assert d["verification"] == "partial"
    assert d["confidence"] == {"score": 0.6, "band": "medium"}
    assert d["sources_count"] == 3
    assert d["kind"] == "article" and d["page_diff"] is None
    assert d["evidence"] == {
        "quote": "A verbatim quote.", "verified": True, "side": "before", "section": "Plans > Pro"
    }  # fmt: skip
    assert [(e["slug"], e["primary"]) for e in d["entities"]] == [
        ("sonatype", True), ("sonatype-nexus", False)
    ]  # fmt: skip
    assert d["source"]["name"] == "Default Feed"


def test_unverified_quote_is_flagged(client, build):
    sig = build.signal("x", quote_verified=False)
    ev = client.get(f"/api/signals/{sig.id}").json()["evidence"]
    assert ev["verified"] is False and ev["side"] == "after"


def test_band_computed_when_missing(client, build):
    sig = build.signal("x", confidence=0.4)
    assert client.get(f"/api/signals/{sig.id}").json()["confidence"]["band"] == "low"


def test_detail_404s(client, build):
    canonical = build.signal("canonical")
    dup = build.signal("dup")
    dup.dedup_cluster_id = canonical.id
    raw = build.signal("raw", enriched=False)
    build.s.flush()

    assert client.get(f"/api/signals/{canonical.id}").status_code == 200
    assert client.get(f"/api/signals/{dup.id}").status_code == 404
    assert client.get(f"/api/signals/{raw.id}").status_code == 404
    assert client.get("/api/signals/999999").status_code == 404


def test_entities_counts_include_products(client, build):
    jfrog = build.entity("jfrog", "JFrog", relationship=Relationship.SELF, is_self=True)
    sonatype = build.entity("sonatype", "Sonatype")
    nexus = build.entity("sonatype-nexus", "Nexus Repository", parent=sonatype)
    build.entity("untracked", "Untracked", is_tracked=False)
    a, b = build.signal("a", hours_ago=5), build.signal("b", hours_ago=2)
    build.link(a, sonatype)
    build.link(b, nexus)
    build.link(b, sonatype, primary=False)
    build.link(build.signal("irrelevant", relevant=False), sonatype)

    data = client.get("/api/entities").json()

    assert [e["slug"] for e in data] == ["jfrog", "sonatype"]  # self first; companies only
    assert data[0]["signal_count"] == 0 and data[0]["last_signal_at"] is None
    assert data[1]["signal_count"] == 2
    assert data[1]["type"] == EntityType.COMPANY
    assert data[1]["last_signal_at"].startswith("2026-10-21T06:00")
    assert jfrog.is_self


def test_nav_counts(client, build):
    build.signal("new")
    build.signal("approved", status=ReviewStatus.APPROVED)
    build.signal("irrelevant", relevant=False)
    assert client.get("/api/nav").json() == {"signals": 2, "review_queue": 1, "radar_new": 0}


def test_self_product_terms(client, build):
    jfrog = build.entity("jfrog", "JFrog", relationship=Relationship.SELF, is_self=True)
    build.entity("jfrog-xray", "JFrog Xray", parent=jfrog, aliases=["Xray"])
    build.entity("jfrog-advanced-security", "JFrog Advanced Security", parent=jfrog)
    build.entity("jfrog-ml", "JFrog ML", parent=jfrog, aliases=["Qwak"])
    # Discontinued: not highlighted as ours.
    build.entity(
        "jfrog-pipelines", "JFrog Pipelines", parent=jfrog, discontinued_on=date(2026, 5, 1)
    )
    other = build.entity("github", "GitHub")
    build.entity("github-actions", "GitHub Actions", parent=other)

    data = client.get("/api/self").json()

    assert data["slug"] == "jfrog"
    # Longest first; "ML" alone is too short; other companies' products are not included.
    assert data["product_terms"] == [
        "JFrog Advanced Security",
        "JFrog Xray",
        "JFrog ML",
        "Qwak",
        "Xray",
    ]
    assert data["bare_terms"] == ["Advanced Security"]  # "Xray" is an alias, so not bare


def test_self_404_without_self_entity(client, build):
    build.entity("github", "GitHub")
    assert client.get("/api/self").status_code == 404
