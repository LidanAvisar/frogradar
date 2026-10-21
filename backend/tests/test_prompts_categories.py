"""Category definitions: identical in both enrich prompts and complete (no LLM)."""

import re

from app.core.llm import load_prompt
from app.db.enums import SignalCategory


def _definitions(name: str) -> str:
    system = load_prompt(name).system or ""
    start = system.index("- category: one of $categories.")
    return system[start : system.index("\n- summary:", start)]


def test_enrich_prompts_define_every_category_identically():
    signal, diff = _definitions("enrich_signal"), _definitions("enrich_page_diff")
    assert signal == diff
    defined = set(re.findall(r"^\s+([a-z_]+) = ", signal, re.MULTILINE))
    assert defined == {c.value for c in SignalCategory} - {"other"}
    assert load_prompt("enrich_signal").id == "enrich_signal.v2"
    assert load_prompt("enrich_page_diff").id == "enrich_page_diff.v3"


def test_verify_v2_puts_jfrog_analysis_out_of_scope():
    p = load_prompt("verify_signal")
    assert p.id == "verify_signal.v2"
    assert "never list as unsupported: statements about JFrog" in (p.system or "")
