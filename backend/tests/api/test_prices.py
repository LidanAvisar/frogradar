from app.api.prices import price_changes

HEADER = "Page: P\nFormat: ...\n\n"


def pairs(text: str) -> list[tuple[str, str, str]]:
    return [(c.label, c.before, c.after) for c in price_changes(HEADER + text)]


def test_same_label_pairs():
    text = """## Change 1 of 1
[before]
[Product Packages > Nexus > Pro Edition] Starting at $1,620 / year + consumption
[Product Packages > Guide > Pro] Starting at $1,200 / year
[after]
[Product Packages > Nexus > Pro Edition] Starting at $1,950 / year + consumption
[Product Packages > Guide > Pro] Starting at $1,200 / year Up to 5,000 credits
"""
    # Guide price unchanged → not a change. Generic first segment dropped from the label.
    assert pairs(text) == [("Nexus > Pro Edition", "$1,620 / year", "$1,950 / year")]


def test_restructured_ambiguous_split_gives_nothing():
    # Sonatype 2026-10: "Pro Edition" split into Cloud and Self-Hosted → two candidates.
    text = """## Change 1 of 1
[before]
[Product Packages > Nexus Repository > Pro Edition] Starting at $1,620 / year + consumption
[after]
[Product Packages > Nexus Repository > Cloud > Pro Edition] Starting at $1,950 / year
[Product Packages > Nexus Repository > Self-Hosted > Pro Edition] Starting at $7,500 / year
"""
    assert pairs(text) == []


def test_unique_nested_label_pairs():
    text = """## Change 1 of 1
[before]
[Plans > Pro Edition] Starting at $1,620 / year
[after]
[Plans > Cloud > Pro Edition] Starting at $1,950 / year
"""
    assert pairs(text) == [("Cloud > Pro Edition", "$1,620 / year", "$1,950 / year")]


def test_comparison_rows_and_usage_tables_skipped():
    text = """## Change 1 of 1
[before]
[Pricing > Tiers] Free $0 Team $4 per user/month Enterprise $21 per user/month
[Pricing > Usage] Tier 1 $1.10 Tier 2 $.90
[after]
[Pricing > Tiers] Free $0 Team $5 per user/month Enterprise $21 per user/month
[Pricing > Usage] Tier 1 $1.20 Tier 2 $.90
"""
    assert pairs(text) == []


def test_unlabeled_single_lines_pair():
    text = """## Change 1 of 1
[context]
Team plan
[before]
$4 per user/month
[after]
$5 per user/month
"""
    assert pairs(text) == [("", "$4 per user/month", "$5 per user/month")]


def test_unlabeled_multiple_priced_lines_ambiguous():
    text = """## Change 1 of 1
[before]
Starting at $135 + consumption per month/billed annually
[after]
Starting at $1,620 / year + consumption Guide
Starting at $1,200 / year Firewall
"""
    assert pairs(text) == []


def test_no_prices_and_removed_only():
    text = """## Change 1 of 2
[before]
Try the Copilot-powered platform
[after]
Try GitHub, the complete developer platform

## Change 2 of 2
[before]
[Plans > Team] $4 per user/month
[after]
(removed)
"""
    assert pairs(text) == []


def test_pairs_across_hunks_and_dedup():
    hunk = """[before]
[Plans > Pro] $10 / month
[after]
[Plans > Pro] $12 / month
"""
    text = f"## Change 1 of 2\n{hunk}\n## Change 2 of 2\n{hunk}"
    assert pairs(text) == [("Plans > Pro", "$10 / month", "$12 / month")]
