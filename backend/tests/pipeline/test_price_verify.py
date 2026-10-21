"""Code check of model-proposed price changes against the diff (no DB, no LLM) and the hard
length limits of takeaway / display_title."""

import pytest
from pydantic import ValidationError

from app.pipeline.page_diff import diff_hunks
from app.pipeline.price_verify import verify_price_changes
from app.pipeline.schemas import ProposedPriceChange, enrich_schema

HEADER = "Page: X\nURL: https://x.test\nBefore: 2026-07-29 (wayback)\nAfter: 2026-10-21 (live)\n\n"

LABELED = HEADER + (
    "## Change 1 of 1\n[before]\n"
    "[Product Packages > Nexus Repository > Pro Edition] Starting at $1,620 / year + consumption\n"
    "[Product Packages > Guide > Pro] Starting at $1,200 / year\n"
    "[after]\n"
    "[Product Packages > Nexus Repository > Cloud > Pro Edition] "
    "Starting at $1,950 / year + consumption\n"
    "[Product Packages > Nexus Repository > Self-Hosted > Pro Edition] Starting at $7,500 / year\n"
    "[context]\n[Product Packages > Guide > Enterprise] Custom Pricing $3,000 / year\n"
)

UNLABELED = HEADER + (
    "## Change 1 of 2\n[before]\nCopilot Business $19 per user/month\n"
    "[after]\nCopilot Business $ 21 per user/month\n[context]\nEnterprise $39 per user/month\n\n"
    "## Change 2 of 2\n[before]\nActions minutes 2,000\n[after]\nActions minutes 3,000\n\n"
    "## Moved between sections\n- Secret scanning: Team $4 → Enterprise $21\n"
)


def _pc(before, after, label=""):
    return ProposedPriceChange(label=label, before=before, after=after)


def test_accepts_a_real_change_and_takes_the_label_from_the_page():
    ok, dropped = verify_price_changes([_pc("$1,620 / year", "$1,950 / year", "Pro")], LABELED)
    assert ok == [
        {"label": "Nexus Repository > Cloud > Pro Edition", "before": "$1,620 / year",
         "after": "$1,950 / year"}
    ]  # fmt: skip
    assert dropped == []


def test_rejects_before_not_in_before_and_after_only_in_context():
    ok, dropped = verify_price_changes(
        [
            _pc("$1,500 / year", "$1,950 / year"),  # invented old price
            _pc("$1,200 / year", "$3,000 / year"),  # new value only in a [context] line
            _pc("$1,620", "$1,620"),  # no change
            _pc("Pro", "Pro+"),  # not a price
        ],
        LABELED,
    )
    assert ok == []
    assert [d["reason"] for d in dropped] == [
        "before_not_in_before", "after_not_in_same_hunk", "no_change", "not_a_price",
    ]  # fmt: skip


def test_rejects_pairs_across_hunks_and_ignores_the_moved_list():
    ok, dropped = verify_price_changes([_pc("2,000", "$21"), _pc("$4", "$21")], UNLABELED)
    assert ok == []
    assert [d["reason"] for d in dropped] == ["after_not_in_same_hunk", "before_not_in_before"]


def test_unlabeled_diff_uses_the_model_label_only_if_it_is_in_the_hunk():
    ok, _ = verify_price_changes(
        [
            _pc("$19 per user/month", "$21 per user/month", "Copilot Business"),
            _pc("2,000", "3,000", "Free plan"),  # "Free plan" is not in that hunk
        ],
        UNLABELED,
    )
    assert ok[0]["label"] == "Copilot Business" and ok[0]["after"] == "$21 per user/month"
    assert ok[1]["label"] == ""


def test_ambiguous_section_is_dropped():
    diff = HEADER + (
        "## Change 1 of 1\n[before]\n[Nexus > Pro] Starting at $1,620\n[after]\n"
        "[Nexus > Cloud > Pro] Starting at $1,950\n[Nexus > Self-Hosted > Pro] Starting at $1,950\n"
    )
    ok, dropped = verify_price_changes([_pc("$1,620", "$1,950")], diff)
    assert ok == [] and dropped[0]["reason"] == "ambiguous_section"


def test_heading_lines_give_the_section():
    diff = HEADER + (
        "## Change 1 of 1\n[context]\n# Plans > Team\n[before]\n$4 per user/month\n"
        "[after]\n$5 per user/month\n"
    )
    hunk = diff_hunks(diff)[0]
    assert [ln.section for ln in hunk] == [("Plans", "Team"), ("Plans", "Team")]
    ok, _ = verify_price_changes([_pc("$4 per user/month", "$5 per user/month")], diff)
    assert ok[0]["label"] == "Plans > Team"


_ENRICHED = {
    "category": "pricing_change", "summary": "x" * 50, "why_jfrog_cares": "y" * 50, "impact": 3,
    "impact_rationale": "r", "evidence_quote": "a quote here", "is_claim": False,
    "entities": [], "capabilities": [], "takeaway": "t" * 140,
}  # fmt: skip


def test_takeaway_and_display_title_hard_limits():
    article = enrich_schema(["sonatype"], ["sbom"])
    article.model_validate(_ENRICHED)
    with pytest.raises(ValidationError, match="takeaway"):
        article.model_validate({**_ENRICHED, "takeaway": "t" * 161})
    with pytest.raises(ValidationError, match="takeaway"):
        article.model_validate({k: v for k, v in _ENRICHED.items() if k != "takeaway"})

    diff = enrich_schema(["sonatype"], ["sbom"], page_diff=True)
    diff.model_validate({**_ENRICHED, "display_title": "GitHub splits secret scanning in two"})
    with pytest.raises(ValidationError, match="display_title"):
        diff.model_validate({**_ENRICHED, "display_title": "d" * 91})
    with pytest.raises(ValidationError, match="display_title"):
        diff.model_validate(_ENRICHED)  # required for page diffs


def test_different_billing_units_are_not_a_price_change():
    diff = HEADER + (
        "## Change 1 of 1\n[before]\nPro Starting at $135 + consumption per month/billed annually\n"
        "Guide $18.67 per user/month billed annually\nTeam $4 per user/month\n"
        "[after]\nPro Starting at $1,620 / year + consumption\nGuide Starting at $1,200 / year\n"
        "Team $5 per user/month\n"
    )
    ok, dropped = verify_price_changes(
        [
            _pc("Starting at $135 + consumption per month/billed annually",
                "Starting at $1,620 / year + consumption"),  # monthly vs yearly display
            # per seat vs flat
            _pc("$18.67 per user/month billed annually", "Starting at $1,200 / year"),
            _pc("$4 per user/month", "$5 per user/month"),  # same unit: a real change
        ],
        diff,
    )  # fmt: skip
    assert [d["reason"] for d in dropped] == ["unit_mismatch", "unit_mismatch"]
    assert [(c["before"], c["after"]) for c in ok] == [("$4 per user/month", "$5 per user/month")]
