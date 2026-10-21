import copy
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.core import catalog
from app.core.catalog import (
    CapabilitiesFile,
    CompetitorsFile,
    ConfigError,
    LLMRoutingFile,
    SourcesFile,
)
from app.core.config import get_settings
from app.db.enums import Relationship


def _raw(name: str) -> Any:
    return yaml.safe_load((get_settings().config_dir / name).read_text())


# ---------- the real files ----------


def test_real_config_files_validate():
    competitors = catalog.load_competitors()
    assert catalog.load_sources().sources
    assert sum(len(c) for c in catalog.load_capabilities().segments.values()) >= 30
    routing = catalog.load_llm_routing()
    assert routing.embedding.dimensions == 1536

    by_slug = {c.slug: c for c in competitors.companies}
    assert by_slug["jfrog"].relationship is Relationship.SELF
    assert by_slug["github"].relationship is Relationship.COOPETITION
    assert by_slug["hugging-face"].relationship is Relationship.COOPETITION
    plan_competitors = {
        "sonatype", "github", "gitlab", "snyk", "cloudsmith", "docker", "chainguard",
        "endor-labs", "socket", "hugging-face",
    }  # fmt: skip
    assert plan_competitors <= by_slug.keys()


def test_cloud_providers_link_only_through_products():
    by_slug = {c.slug: c for c in catalog.load_competitors().companies}
    for slug, product in [
        ("aws", "aws-codeartifact"),
        ("microsoft", "azure-artifacts"),
        ("google", "google-artifact-registry"),
    ]:
        company = by_slug[slug]
        assert company.aliases == [] and company.sources == []
        assert [p.slug for p in company.products] == [product]
        assert company.products[0].aliases and company.products[0].sources


def test_every_capability_segment_is_present():
    assert set(catalog.load_capabilities().segments) == {
        "artifact_management", "supply_chain_security", "mlops", "cicd", "distribution",
    }  # fmt: skip


# ---------- competitors.yaml rules ----------


def _competitors() -> dict:
    return copy.deepcopy(_raw("competitors.yaml"))


def test_duplicate_slug_rejected():
    data = _competitors()
    data["companies"][1]["slug"] = data["companies"][0]["slug"]
    with pytest.raises(ValidationError, match="duplicate slug"):
        CompetitorsFile.model_validate(data)


def test_product_slug_clashing_with_company_rejected():
    data = _competitors()
    data["companies"][0]["products"][0]["slug"] = "sonatype"
    with pytest.raises(ValidationError, match="duplicate slug"):
        CompetitorsFile.model_validate(data)


@pytest.mark.parametrize("n_self", [0, 2])
def test_exactly_one_self(n_self):
    data = _competitors()
    for c in data["companies"]:
        c["relationship"] = "competitor"
    for c in data["companies"][:n_self]:
        c["relationship"] = "self"
    with pytest.raises(ValidationError, match="exactly one"):
        CompetitorsFile.model_validate(data)


def test_duplicate_source_url_rejected():
    data = _competitors()
    data["companies"][1]["sources"].append(dict(data["companies"][0]["sources"][0]))
    with pytest.raises(ValidationError, match="duplicate source url"):
        CompetitorsFile.model_validate(data)


@pytest.mark.parametrize("options", [{}, {"repos": []}, {"repos": "jfrog-cli"}, {"repos": [1]}])
def test_github_source_needs_repos(options):
    data = _competitors()
    data["companies"][0]["sources"].append(
        {"name": "X GitHub", "url": "https://github.com/x", "kind": "github",
         "fetch_method": "github", "options": options}
    )  # fmt: skip
    with pytest.raises(ValidationError, match="needs options.repos"):
        CompetitorsFile.model_validate(data)


def test_every_real_github_source_lists_repos():
    for src in catalog.load_competitors().all_sources():
        if src.fetch_method == "github":
            assert src.options["repos"], src.name


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trust_score", 1.5),
        ("kind", "rumor"),
        ("fetch_method", "carrier_pigeon"),
        ("url", "ftp://x"),
    ],
)
def test_bad_source_values_rejected(field, value):
    data = _competitors()
    data["companies"][0]["sources"][0][field] = value
    with pytest.raises(ValidationError):
        CompetitorsFile.model_validate(data)


def test_unknown_segment_and_unknown_key_rejected():
    data = _competitors()
    data["companies"][0]["segments"] = ["space_travel"]
    with pytest.raises(ValidationError):
        CompetitorsFile.model_validate(data)
    data = _competitors()
    data["companies"][0]["ceo"] = "someone"
    with pytest.raises(ValidationError, match="Extra inputs"):
        CompetitorsFile.model_validate(data)


def test_trust_score_defaults_by_kind():
    src = catalog.SourceConfig(name="x", url="https://x", kind="press", fetch_method="rss")
    assert src.effective_trust == 0.7


# ---------- sources.yaml / capabilities.yaml ----------


def test_sources_duplicate_url_rejected():
    data = copy.deepcopy(_raw("sources.yaml"))
    data["sources"].append(dict(data["sources"][0]))
    with pytest.raises(ValidationError, match="duplicate source url"):
        SourcesFile.model_validate(data)


def test_duplicate_capability_slug_rejected():
    data = copy.deepcopy(_raw("capabilities.yaml"))
    data["segments"]["mlops"].append(dict(data["segments"]["artifact_management"][0]))
    with pytest.raises(ValidationError, match="duplicate capability slug"):
        CapabilitiesFile.model_validate(data)


# ---------- llm_routing.yaml ----------


def _routing() -> dict:
    return copy.deepcopy(_raw("llm_routing.yaml"))


@pytest.mark.parametrize("writer", ["bulk", "synthesis"])
def test_verify_must_use_a_different_provider(writer):
    data = _routing()
    data["tasks"]["verify"]["model"] = data["tasks"][writer]["model"]
    with pytest.raises(ValidationError, match="verify provider"):
        LLMRoutingFile.model_validate(data)


def test_verify_provider_differs_from_both_writers_in_real_config():
    tasks = catalog.load_llm_routing().tasks
    assert tasks.verify.provider not in {tasks.bulk.provider, tasks.synthesis.provider}


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("embedding", "dimensions"), 3072),  # over pgvector's HNSW limit
        (("tasks", "bulk", "model"), "no-provider-prefix"),
        (("tasks", "bulk", "temperature"), 5),
    ],
)
def test_bad_routing_values_rejected(path, value):
    data = _routing()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        LLMRoutingFile.model_validate(data)


def test_missing_task_rejected():
    data = _routing()
    del data["tasks"]["verify"]
    with pytest.raises(ValidationError):
        LLMRoutingFile.model_validate(data)


def test_pricing_validated():
    data = _routing()
    data["pricing"] = {"openai/x": {"input_per_mtok_usd": 0.5, "output_per_mtok_usd": 2}}
    assert LLMRoutingFile.model_validate(data).pricing["openai/x"].output_per_mtok_usd == 2
    data["pricing"] = {"openai/x": {"input_per_mtok_usd": -1}}
    with pytest.raises(ValidationError):
        LLMRoutingFile.model_validate(data)


def test_env_var_overrides_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_BULK", "gemini/gemini-3.5-flash")
    get_settings.cache_clear()
    try:
        assert catalog.load_llm_routing().tasks.bulk.model == "gemini/gemini-3.5-flash"
    finally:
        get_settings.cache_clear()


# ---------- entity-linking match rules (competitors.yaml) ----------


def _socket(data: dict) -> dict:
    return next(c for c in data["companies"] if c["slug"] == "socket")


def test_real_match_rules():
    by_slug = {e.slug: e for c in catalog.load_competitors().companies for e in (c, *c.products)}
    socket = by_slug["socket"].match_rules["Socket"]
    assert socket["case_sensitive"] and socket["min_mentions"] >= 2
    assert by_slug["microsoft"].match_rules["Microsoft"]["enabled"] is False
    assert by_slug["github-packages"].alias_names == ["GitHub Container Registry", "GHCR"]
    # Default-rule terms are not stored.
    assert "Snyk" not in by_slug["snyk"].match_rules


def test_alias_object_form():
    data = _competitors()
    _socket(data)["aliases"] = ["socket.dev", {"alias": "SKT", "case_sensitive": True}]
    socket = next(c for c in CompetitorsFile.model_validate(data).companies if c.slug == "socket")
    assert socket.alias_names == ["socket.dev", "SKT"]
    assert socket.match_rules["SKT"]["case_sensitive"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"alias": "X", "case_sensitive": True},  # too short
        {"alias": "Sock", "min_mentions": 0},
        {"alias": "Sock", "casesensitive": True},  # typo -> extra field
        {"case_sensitive": True},  # no alias
    ],
)
def test_bad_alias_rule_rejected(bad):
    data = _competitors()
    _socket(data)["aliases"] = [bad]
    with pytest.raises(ValidationError):
        CompetitorsFile.model_validate(data)


def test_bad_name_rule_rejected():
    data = _competitors()
    _socket(data)["match"] = {"min_mentions": "lots"}
    with pytest.raises(ValidationError):
        CompetitorsFile.model_validate(data)


def test_alias_duplicating_name_rejected():
    data = _competitors()
    _socket(data)["aliases"] = ["socket"]
    with pytest.raises(ValidationError, match="duplicate alias"):
        CompetitorsFile.model_validate(data)


# ---------- pipeline.yaml ----------


def test_pipeline_config():
    p = catalog.load_pipeline()
    assert p.dedup.cross_source_threshold <= p.dedup.same_source_threshold
    assert p.embedding.max_chars > 0


def test_pipeline_thresholds_ordered():
    data = copy.deepcopy(_raw("pipeline.yaml"))
    data["dedup"]["same_source_threshold"] = 0.8
    with pytest.raises(ValidationError, match="same_source_threshold"):
        catalog.PipelineFile.model_validate(data)


# ---------- loader errors ----------


def test_loader_error_names_the_file(config_copy):
    (config_copy / "sources.yaml").write_text("sources: [{name: x}]")
    with pytest.raises(ConfigError, match="sources.yaml"):
        catalog.load_sources(config_copy)


def test_missing_file_is_a_config_error(config_copy):
    (config_copy / "capabilities.yaml").unlink()
    with pytest.raises(ConfigError, match="capabilities.yaml"):
        catalog.load_capabilities(config_copy)
