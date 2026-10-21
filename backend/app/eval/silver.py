"""Silver labels: the synthesis model (stronger than the bulk model that runs triage/enrich)
labels the eval set and the dedup pairs independently.

- Items: relevant, category, impact, primary_entity – using the pipeline prompts' own rubric
  text, extracted from triage.v1 / enrich_signal.v1 / enrich_page_diff.v2 at render time, so the
  two can't drift. The labeler sees the source text (same 12,000-character cut as enrich) and
  never any pipeline output (summary, triage decision, category, …).
- Pairs: same_story y/n on the same text the embedding saw (title + first 2,000 characters).

These are agreement references, not human gold labels (README "Evaluation").
Calls go through the gateway (cached by content hash, logged with cost) under an eval run row.
"""

import logging
from string import Template
from typing import Any, Literal, Self

from pydantic import Field, create_model, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.catalog import load_pipeline
from app.core.llm import Prompt, complete, load_prompt
from app.core.llm.routing import resolve
from app.db.enums import SignalCategory
from app.db.models import Signal
from app.eval.common import Estimate, eval_run, parallel_map, probe
from app.eval.files import (
    NONE_ENTITY,
    LabelRow,
    PairRow,
    read_labels,
    read_pairs,
    write_labels,
    write_pairs,
)
from app.pipeline.llm_steps import load_input
from app.pipeline.page_diff import is_page_diff
from app.pipeline.prompting import Catalog, SignalInput, delimit
from app.pipeline.schemas import Short, _Out
from app.pipeline.scope import eval_set

logger = logging.getLogger(__name__)

ITEM_PROMPT = "silver_label"
PAIR_PROMPT = "silver_same_story"
TASK = "synthesis"


class RubricError(ValueError):
    """A marker the rubric extraction relies on is missing from a pipeline prompt."""


def between(text: str, start: str, end: str, *, keep_start: bool = True) -> str:
    """The text from `start` up to (not including) `end`."""
    i = text.find(start)
    j = text.find(end, i + len(start)) if i >= 0 else -1
    if i < 0 or j < 0:
        raise RubricError(f"marker not found: {start!r} … {end!r}")
    return text[i if keep_start else i + len(start) : j].strip()


def rubric(catalog: Catalog, page_diff: bool) -> dict[str, str]:
    """The pipeline prompts' rubric text ($$ → $, $companies filled in)."""
    triage = load_prompt("triage").system or ""
    enrich = load_prompt("enrich_page_diff" if page_diff else "enrich_signal").system or ""

    def fill(block: str) -> str:
        return Template(block).safe_substitute(companies=catalog.companies_text)

    scope = between(triage, "JFrog's market has five segments:", "Decision rule:")
    impact = between(
        enrich, "- impact (1-5), using this rubric:", "- impact_rationale:", keep_start=False
    )
    entity = between(
        load_prompt("enrich_signal").system or "",
        "- entities:",
        "- new_entities:",
        keep_start=False,
    )
    diff = ""
    if page_diff:
        diff = (
            "\nThis item is a diff of a watched page. "
            + between(enrich, "How to read the diff:", "JFrog's portfolio:")
            + "\n"
        )
    return {
        "relevance_scope": fill(scope),
        "impact_rubric": fill(impact),
        "entity_rule": " ".join(fill(entity).split()),
        "diff_rules": fill(diff),
    }


def label_schema(entity_slugs: list[str]) -> type[_Out]:
    slug = Literal[tuple(sorted({*entity_slugs, NONE_ENTITY}))]

    def check(self: Any) -> Self:
        if self.relevant and (
            self.category is None or self.impact is None or self.primary_entity is None
        ):
            raise ValueError("relevant items need category, impact and primary_entity")
        return self

    return create_model(
        "SilverLabel",
        __base__=_Out,
        __validators__={"_complete": model_validator(mode="after")(check)},
        relevant=(bool, ...),
        reason=(Short, Field(description="One sentence.")),
        category=(SignalCategory | None, None),
        impact=(int | None, Field(default=None, ge=1, le=5)),
        primary_entity=(slug | None, None),
    )


class SameStory(_Out):
    same_story: bool
    reason: Short = Field(description="One sentence.")


def item_messages(
    sig: SignalInput, meta: dict | None, catalog: Catalog
) -> tuple[Prompt, list[dict[str, str]]]:
    prompt = load_prompt(ITEM_PROMPT)
    tag, text = delimit(sig.raw_text, load_pipeline().enrich.max_chars)
    return prompt, prompt.messages(
        **rubric(catalog, is_page_diff(meta)),
        categories=", ".join(c.value for c in SignalCategory),
        entities=catalog.entities_text,
        source=sig.source,
        source_kind=sig.source_kind,
        published=sig.published,
        title=sig.title,
        tag=tag,
        text=text,
    )


def pair_messages(a: SignalInput, b: SignalInput) -> tuple[Prompt, list[dict[str, str]]]:
    prompt = load_prompt(PAIR_PROMPT)
    chars = load_pipeline().embedding.max_chars
    values: dict[str, str] = {}
    for p, sig in (("a", a), ("b", b)):
        tag, text = delimit(sig.raw_text, chars)
        values |= {
            f"{p}_tag": tag,
            f"{p}_text": text,
            f"{p}_title": sig.title,
            f"{p}_source": sig.source,
            f"{p}_published": sig.published,
        }
    return prompt, prompt.messages(**values)


def label_source() -> str:
    from app.core.llm import get_gateway

    return f"model:{resolve(TASK, get_gateway().routing).model}"


# ---------- items ----------


def _item_inputs(s: Session, ids: list[int]) -> dict[int, tuple[SignalInput, dict]]:
    metas = dict(s.execute(select(Signal.id, Signal.meta).where(eval_set(ids))).all())
    return {i: (load_input(s, i), metas.get(i) or {}) for i in ids}


def estimate_items(factory: sessionmaker[Session], rows: list[LabelRow]) -> Estimate:
    est = Estimate()
    todo = [r.id for r in rows if not r.is_labeled()]
    with factory() as s:
        catalog = Catalog.load(s)
        inputs = _item_inputs(s, todo)
    schema = label_schema(catalog.entity_slugs)
    for i in todo:
        _, messages = item_messages(*inputs[i], catalog)
        probe(est, TASK, messages, schema, tokens_out=200)
    return est


def label_items(factory: sessionmaker[Session]) -> dict[str, Any]:
    rows = read_labels()
    todo = [r for r in rows if not r.is_labeled()]
    with factory() as s:
        catalog = Catalog.load(s)
        inputs = _item_inputs(s, [r.id for r in todo])
    schema = label_schema(catalog.entity_slugs)
    source = label_source()

    def one(row: LabelRow) -> Any:
        prompt, messages = item_messages(*inputs[row.id], catalog)
        out = complete(TASK, ITEM_PROMPT, messages, schema, prompt_version=prompt.id)
        return prompt.id, out

    with eval_run(factory, "eval_silver_items") as counts:
        results = parallel_map(one, todo)
        failed = 0
        for row, res in zip(todo, results, strict=True):
            if isinstance(res, Exception):
                failed += 1
                continue
            version, out = res
            row.relevant = "y" if out.relevant else "n"
            row.category = str(out.category) if out.relevant and out.category else ""
            row.impact = str(out.impact) if out.relevant and out.impact else ""
            row.primary_entity = str(out.primary_entity) if out.relevant else ""
            row.label_source = f"{source} {version}"
            row.label_reason = " ".join(out.reason.split())
        write_labels(rows)
        counts.update(labeled=len(todo) - failed, failed=failed)
    return counts


# ---------- pairs ----------


def _pair_inputs(s: Session, pairs: list[PairRow]) -> dict[int, SignalInput]:
    ids = sorted({i for p in pairs for i in (p.a_id, p.b_id)})
    return {i: load_input(s, i) for i in ids}


def estimate_pairs(factory: sessionmaker[Session], pairs: list[PairRow]) -> Estimate:
    est = Estimate()
    todo = [p for p in pairs if not p.is_labeled()]
    with factory() as s:
        inputs = _pair_inputs(s, todo)
    for p in todo:
        _, messages = pair_messages(inputs[p.a_id], inputs[p.b_id])
        probe(est, TASK, messages, SameStory, tokens_out=80)
    return est


def label_pairs(factory: sessionmaker[Session]) -> dict[str, Any]:
    pairs = read_pairs()
    todo = [p for p in pairs if not p.is_labeled()]
    with factory() as s:
        inputs = _pair_inputs(s, todo)
    source = label_source()

    def one(p: PairRow) -> Any:
        prompt, messages = pair_messages(inputs[p.a_id], inputs[p.b_id])
        return prompt.id, complete(TASK, PAIR_PROMPT, messages, SameStory, prompt_version=prompt.id)

    with eval_run(factory, "eval_silver_pairs") as counts:
        results = parallel_map(one, todo)
        failed = 0
        for p, res in zip(todo, results, strict=True):
            if isinstance(res, Exception):
                failed += 1
                continue
            version, out = res
            p.same_story = "y" if out.same_story else "n"
            p.label_source = f"{source} {version}"
            p.label_reason = " ".join(out.reason.split())
        write_pairs(pairs)
        counts.update(labeled=len(todo) - failed, failed=failed)
    return counts
