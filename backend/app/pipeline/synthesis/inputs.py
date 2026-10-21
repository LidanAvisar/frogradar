"""What the synthesis model sees about each signal: enrichment outputs only, never raw_text."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Capability, Signal, signal_capabilities
from app.pipeline.prompting import delimit

# Generous cap: ~30 signals × ~1,000 chars. delimit() only cuts beyond this.
BLOCK_MAX_CHARS = 60000


@dataclass(frozen=True)
class SignalFacts:
    id: int
    title: str  # display_title for page diffs, else the source title
    date: str
    category: str
    impact: int | None
    confidence_band: str
    takeaway: str
    summary: str
    why: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def render(self, *, with_capabilities: bool = False) -> str:
        lines = [
            f"[#{self.id}] {self.date} | {self.category} | impact {self.impact} | "
            f"confidence {self.confidence_band}",
            f"Title: {self.title}",
            f"Takeaway: {self.takeaway}",
            f"Summary: {self.summary}",
            f"Why JFrog cares: {self.why}",
        ]
        if with_capabilities:
            lines.append(f"Capabilities: {', '.join(self.capabilities) or '-'}")
        return "\n".join(lines)


def display_title(sig: Signal) -> str:
    title = (sig.meta or {}).get("display_title")
    return title if isinstance(title, str) and title.strip() else sig.title


def facts(session: Session, signals: Sequence[Signal]) -> list[SignalFacts]:
    caps: dict[int, list[str]] = defaultdict(list)
    if signals:
        rows = session.execute(
            select(signal_capabilities.c.signal_id, Capability.slug)
            .join(Capability, Capability.id == signal_capabilities.c.capability_id)
            .where(signal_capabilities.c.signal_id.in_([s.id for s in signals]))
            .order_by(Capability.slug)
        )
        for sid, slug in rows:
            caps[sid].append(slug)
    out = []
    for s in signals:
        when = s.published_at or s.fetched_at
        band = ((s.meta or {}).get("score") or {}).get("band") or "unknown"
        out.append(
            SignalFacts(
                id=s.id,
                title=display_title(s),
                date=when.date().isoformat() if when else "unknown",
                category=str(s.category) if s.category else "other",
                impact=s.impact,
                confidence_band=band,
                takeaway=s.takeaway or "(none)",
                summary=s.summary or "",
                why=s.why_jfrog_cares or "",
                capabilities=tuple(caps.get(s.id, [])),
            )
        )
    return out


def signals_block(
    items: Sequence[SignalFacts], *, with_capabilities: bool = False
) -> dict[str, Any]:
    """{tag, signals}: the rendered signals inside a hash-tagged delimiter (prompting.delimit),
    because summaries are model output derived from untrusted web text."""
    text = "\n\n".join(f.render(with_capabilities=with_capabilities) for f in items)
    tag, body = delimit(text, BLOCK_MAX_CHARS)
    return {"tag": tag, "signals": body}
