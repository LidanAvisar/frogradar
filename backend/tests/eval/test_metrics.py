"""Eval metric calculations (pure functions, no DB, no LLM)."""

import pytest

from app.eval import metrics


def test_binary_counts_precision_recall_and_agreement():
    pred = [True, True, True, False, False]
    ref = [True, False, True, True, False]
    m = metrics.binary(pred, ref)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 1)
    assert m["precision"] == pytest.approx(0.667, abs=1e-3)
    assert m["recall"] == pytest.approx(0.667, abs=1e-3)
    assert m["agreement"] == 0.6 and m["f1"] == pytest.approx(0.667, abs=1e-3)
    empty = metrics.binary([False], [False])
    assert empty["precision"] is None and empty["recall"] is None and empty["f1"] is None


def test_category_agreement_and_confusion_direction():
    m = metrics.category(
        [
            ("product_launch", "feature_update"),
            ("other", "other"),
            ("product_launch", "feature_update"),
        ]
    )
    assert m["agreement"] == pytest.approx(0.333, abs=1e-3)
    assert m["confusion"] == {"feature_update → product_launch": 2}  # silver → pipeline


def test_impact_signed_error_shows_over_rating():
    m = metrics.impact([(4, 3), (3, 3), (5, 3), (2, 3)])  # pipeline, silver
    assert m["exact"] == 0.25 and m["within_1"] == 0.75
    assert m["mean_signed_error"] == 0.5  # (+1 + 0 + 2 - 1) / 4: over-rates
    assert m["mean_abs_error"] == 1.0
    assert m["errors"] == {-1: 1, 0: 1, 1: 1, 2: 1}
    assert metrics.impact([])["mean_signed_error"] is None


def test_entity_strict_and_product_company_family():
    family = {"sonatype-firewall": "sonatype", "github-actions": "github"}
    rows = [
        (1, ["sonatype", "sonatype-firewall"], "sonatype-firewall"),  # strict hit
        (2, ["github"], "github-actions"),  # family hit only
        (3, [], "none"),  # no tracked entity on both sides
        (4, ["snyk"], "none"),  # pipeline linked something the label says isn't there
    ]
    m = metrics.entity(rows, family)
    assert m["strict"] == 0.5 and m["family"] == 0.75
    assert [(x["id"], x["family_match"]) for x in m["misses"]] == [(2, True), (4, False)]


SUMMARY = "Sonatype launched Repository Firewall 2, which blocks malicious npm packages."
WHY = (
    "This competes directly with JFrog Curation at the repository layer; battlecards should "
    "stress Curation's policy gate."
)


def test_claim_attribution_separates_facts_from_jfrog_analysis():
    fact = metrics.attribute_claim("Firewall 2 blocks malicious PyPI packages", SUMMARY, WHY)
    assert fact.field == "summary" and not fact.analysis
    analysis = metrics.attribute_claim("It competes directly with JFrog Curation", SUMMARY, WHY)
    assert analysis.field == "why_jfrog_cares" and analysis.analysis
    assert metrics.attribute_claim("", SUMMARY, WHY).field == "unclear"
    assert metrics.attribute_claim("zebra quantum", SUMMARY, WHY).field == "unclear"


def test_verifier_counts_analysis_only_partials():
    base = {"summary": SUMMARY, "why": WHY, "title": "t"}
    items = [
        {
            **base,
            "id": 1,
            "verdict": "partial",
            "unsupported_claims": ["It competes directly with JFrog Curation"],
        },
        {
            **base,
            "id": 2,
            "verdict": "partial",
            "unsupported_claims": [
                "Firewall 2 blocks malicious PyPI packages",
                "It competes directly with JFrog Curation",
            ],
        },
        {**base, "id": 3, "verdict": "supported", "unsupported_claims": []},
        {**base, "id": 4, "verdict": "unsupported", "unsupported_claims": ["x"]},
    ]
    m = metrics.verifier(items)
    assert m["verdicts"] == {"partial": 2, "supported": 1, "unsupported": 1}
    assert m["partial_or_unsupported"] == 0.75
    assert m["partials"] == 2 and m["analysis_only_partials"] == 1 and m["why_only_partials"] == 1
    assert [p["analysis_only"] for p in m["partial_details"]] == [True, False]


def test_agreement_matrix():
    m = metrics.agreement_matrix(
        ["partial", "supported", "partial"], ["supported", "supported", "partial"]
    )
    assert m["agreement"] == pytest.approx(0.667, abs=1e-3)
    assert m["pairs"]["partial / supported"] == 1


def test_dedup_sweep_weights_by_band_population_and_recommends_threshold():
    pairs = [
        # (similarity, same story, band, band population)
        (0.92, True, "0.90-0.95", 1),
        (0.87, True, "0.85-0.90", 2),
        (0.86, True, "0.85-0.90", 2),
        (0.82, True, "0.80-0.85", 10),  # stands for 5 pairs (10 / 2 sampled)
        (0.81, False, "0.80-0.85", 10),
        (0.70, False, "0.70-0.75", 100),
    ]
    sweep = metrics.dedup_sweep(pairs)
    rows = {r["threshold"]: r for r in sweep["rows"]}
    assert [r["threshold"] for r in sweep["rows"]] == [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    at80 = rows[0.8]
    assert (at80["tp"], at80["fp"], at80["fn"]) == (4, 1, 0)
    # Weighted: TP = 1 + 1 + 1 + 5 = 8, FP = 5 → precision 8/13.
    assert at80["precision"] == pytest.approx(8 / 13, abs=1e-3) and at80["recall"] == 1.0
    # 0.85: TP 3 (weights 1, 1, 1), FN 5 → recall 3/8, precision 1.
    assert rows[0.85]["precision"] == 1.0 and rows[0.85]["recall"] == pytest.approx(0.375)
    assert sweep["recommended"] == 0.85 and "precision" in sweep["rule"]
    assert rows[0.95]["precision"] is None


def test_dedup_sweep_falls_back_to_f1_when_no_threshold_is_precise():
    pairs = [(0.96, False, "x", 1), (0.9, True, "y", 1), (0.9, False, "y", 1)]
    sweep = metrics.dedup_sweep(pairs, min_precision=0.99)
    assert sweep["recommended"] is not None and "F1" in sweep["rule"]


def test_claim_attribution_uses_the_v2_field_prefix():
    s = metrics.attribute_claim("summary: It competes directly with JFrog Curation", SUMMARY, WHY)
    assert s.field == "summary" and not s.analysis  # the verifier's own label wins
    w = metrics.attribute_claim("why_jfrog_cares: overlaps with JFrog Curation", SUMMARY, WHY)
    assert w.field == "why_jfrog_cares" and w.analysis
