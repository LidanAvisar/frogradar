"""Dynamic few-shot examples for the LLM stages (hook; filled from analyst corrections in S13).

The prompt builders always call few_shot_examples() and render whatever it returns, so S13 only
has to implement the selection (e.g. the most similar corrected signals). Examples become part of
the prompt and therefore of the LLM cache key: new corrections naturally produce fresh outputs.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal

Stage = Literal["triage", "enrich", "verify"]


@dataclass(frozen=True)
class FewShot:
    title: str
    excerpt: str  # a short excerpt (never the full raw_text)
    output: dict[str, Any]  # the analyst-corrected output for this stage
    note: str = ""  # what the analyst changed, e.g. "impact 4 -> 2: minor feature"


def few_shot_examples(stage: Stage, signal: Any) -> list[FewShot]:
    """Corrected examples relevant to `signal` for `stage`. None yet (S13)."""
    return []


def render(examples: list[FewShot]) -> str:
    if not examples:
        return ""
    parts = ["\nCorrected examples from JFrog analysts (follow their judgement):"]
    for ex in examples:
        parts.append(f"- Title: {ex.title}\n  Excerpt: {ex.excerpt}")
        if ex.note:
            parts.append(f"  Analyst note: {ex.note}")
        parts.append(f"  Output: {json.dumps(ex.output, ensure_ascii=False)}")
    return "\n".join(parts) + "\n"
