"""signals.raw_text is never returned by the API (CLAUDE.md rule, D25)."""

from app.db.enums import FetchMethod
from app.main import app
from tests.api.conftest import RAW_SENTINEL

FORBIDDEN_FIELDS = {"raw_text", "meta", "embedding", "content_hash", "search_tsv"}


def test_no_endpoint_returns_raw_text(client, build):
    company = build.entity("sonatype", "Sonatype")
    page = build.source("Pricing", method=FetchMethod.PAGE_DIFF)
    sig = build.signal("article")
    diff = build.signal(
        "diff", source=page, meta={"kind": "page_diff"},
        raw_text=f"## Change 1 of 1\n[before]\n$1 {RAW_SENTINEL}\n[after]\n$2 {RAW_SENTINEL}\n",
    )  # fmt: skip
    build.link(sig, company)

    urls = [
        "/api/brief", "/api/brief?days=90&limit=20", "/api/signals", "/api/signals?q=article",
        f"/api/signals?entity={company.slug}", f"/api/signals/{sig.id}", f"/api/signals/{diff.id}",
        "/api/entities", "/api/nav",
    ]  # fmt: skip
    for url in urls:
        r = client.get(url)
        assert r.status_code == 200, url
        assert RAW_SENTINEL not in r.text, url
        assert "Full article body" not in r.text, url
    # The pricing pair itself is extracted (short tokens only).
    pricing = client.get("/api/brief").json()["pricing_watch"]
    assert pricing[0]["changes"] == [{"label": "", "before": "$1", "after": "$2"}]


def test_openapi_schemas_have_no_internal_fields():
    schemas = app.openapi()["components"]["schemas"]
    for name, schema in schemas.items():
        props = set(schema.get("properties", {}))
        assert not props & FORBIDDEN_FIELDS, (name, props & FORBIDDEN_FIELDS)
