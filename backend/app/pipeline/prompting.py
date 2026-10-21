"""Build the messages for triage / enrich / verify from versioned prompt files.

raw_text is untrusted web content. It is placed inside a block whose tag is derived from the
text's own hash (`<source_text_1a2b3c4d5e6f>`): a text can't contain the hash of itself, so it
can't close the block early, and any literal source_text tags inside are defanged as well. The
prompts tell the model to treat the block as data only; the output is schema-constrained and the
pipeline's own checks (quote check, slug validation, second-model verification, code-computed
confidence) don't depend on the model obeying.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.llm import Prompt, load_prompt
from app.db.enums import EntityReviewStatus, Relationship, SignalCategory
from app.db.models import Capability, Entity
from app.pipeline import fewshot

_TAG_LIKE = re.compile(r"<\s*(/?)\s*source_text", re.IGNORECASE)


@dataclass(frozen=True)
class SignalInput:
    id: int
    title: str
    raw_text: str
    source: str
    source_kind: str
    published: str


def delimit(raw_text: str, max_chars: int) -> tuple[str, str]:
    """(tag, text) – text truncated to max_chars with any source_text tags defanged."""
    text = _TAG_LIKE.sub(lambda m: f"[{m.group(1)}source_text", raw_text[:max_chars])
    if len(raw_text) > max_chars:
        text += "\n[... truncated]"
    tag = "source_text_" + hashlib.sha256(text.encode()).hexdigest()[:12]
    return tag, text


@dataclass(frozen=True)
class Catalog:
    """Entities and capabilities rendered into the enrich prompt (loaded once per run)."""

    entity_slugs: list[str]
    capability_slugs: list[str]
    relationship: dict[str, str]  # slug -> relationship
    entities_text: str
    capabilities_text: str
    companies_text: str
    jfrog_portfolio: str

    @classmethod
    def load(cls, session: Session) -> "Catalog":
        entities = session.scalars(
            select(Entity)
            .where(Entity.review_status == EntityReviewStatus.APPROVED)
            .order_by(Entity.slug)
        ).all()
        by_id = {e.id: e for e in entities}
        lines, portfolio = [], []
        for e in entities:
            parent = by_id[e.parent_id].slug if e.parent_id in by_id else "-"
            lines.append(f"{e.slug} | {e.name} | {e.type} | {parent} | {e.relationship}")
            if e.relationship == Relationship.SELF and e.parent_id is not None:
                portfolio.append(f"- {e.name}: {e.description or '(no description)'}")
        caps = session.scalars(select(Capability).order_by(Capability.segment, Capability.slug))
        caps = caps.all()
        companies = sorted(e.name for e in entities if e.parent_id is None and not e.is_self)
        return cls(
            entity_slugs=[e.slug for e in entities],
            capability_slugs=[c.slug for c in caps],
            relationship={e.slug: str(e.relationship) for e in entities},
            entities_text="\n".join(lines),
            capabilities_text="\n".join(f"{c.slug} | {c.name} | {c.segment}" for c in caps),
            companies_text=", ".join(companies),
            jfrog_portfolio="\n".join(portfolio),
        )


def _common(sig: SignalInput, max_chars: int) -> dict[str, Any]:
    tag, text = delimit(sig.raw_text, max_chars)
    return {
        "tag": tag,
        "text": text,
        "title": sig.title,
        "source": sig.source,
        "source_kind": sig.source_kind,
        "published": sig.published,
    }


def triage_messages(
    sig: SignalInput, catalog: Catalog, max_chars: int
) -> tuple[Prompt, list[dict[str, str]]]:
    prompt = load_prompt("triage")
    examples = fewshot.render(fewshot.few_shot_examples("triage", sig))
    return prompt, prompt.messages(
        **_common(sig, max_chars), companies=catalog.companies_text, examples=examples
    )


def enrich_messages(
    sig: SignalInput, catalog: Catalog, max_chars: int, prompt_name: str = "enrich_signal"
) -> tuple[Prompt, list[dict[str, str]]]:
    prompt = load_prompt(prompt_name)
    examples = fewshot.render(fewshot.few_shot_examples("enrich", sig))
    return prompt, prompt.messages(
        **_common(sig, max_chars),
        jfrog_portfolio=catalog.jfrog_portfolio,
        categories=", ".join(c.value for c in SignalCategory),
        entities=catalog.entities_text,
        capabilities=catalog.capabilities_text,
        examples=examples,
    )


def verify_messages(
    sig: SignalInput, summary: str, why: str, quote: str, max_chars: int
) -> tuple[Prompt, list[dict[str, str]]]:
    prompt = load_prompt("verify_signal")
    return prompt, prompt.messages(
        **_common(sig, max_chars),
        summary=summary,
        why_jfrog_cares=why,
        evidence_quote=quote,
    )
