"""Citation check for synthesized items (code, no LLM).

An item is kept only if EVERY id it cites is in the allowed input set (the exact signals the model
was given: usable, and for battlecards linked to the competitor). One invalid id drops the whole
item (reason invalid_citation): an invented or misplaced id is a sign of fabrication, so the
remaining ids don't rescue it. The detail says why each bad id is bad:
  unknown        – no such signal
  not_usable     – exists, but not visible / not relevant / unsupported
  wrong_entity   – usable, but not about this competitor (battlecards)
  not_in_input   – usable, but not among the signals the model was given
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Signal
from app.pipeline.scope import usable


@dataclass(frozen=True)
class Claim:
    """One synthesized statement to check: `key` is stable within one output ("overview.2")."""

    key: str
    text: str
    signal_ids: tuple[int, ...]
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "text": self.text,
            "signal_ids": list(self.signal_ids),
            **self.extra,
        }


@dataclass
class Checked:
    kept: list[Claim] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def drop(self, claim: Claim, reason: str, **detail: Any) -> None:
        self.dropped.append({**claim.as_dict(), "reason": reason, **detail})


def classify_ids(
    session: Session,
    ids: Iterable[int],
    *,
    entity_signal_ids: Collection[int] | None = None,
) -> dict[int, str]:
    """Why each id (all of them outside the allowed set) is invalid."""
    ids = sorted(set(ids))
    if not ids:
        return {}
    existing = set(session.scalars(select(Signal.id).where(Signal.id.in_(ids))))
    ok = set(session.scalars(select(Signal.id).where(Signal.id.in_(ids), usable())))
    out: dict[int, str] = {}
    for i in ids:
        if i not in existing:
            out[i] = "unknown"
        elif i not in ok:
            out[i] = "not_usable"
        elif entity_signal_ids is not None and i not in entity_signal_ids:
            out[i] = "wrong_entity"
        else:
            out[i] = "not_in_input"
    return out


def check_citations(
    session: Session,
    claims: Sequence[Claim],
    allowed: Collection[int],
    *,
    entity_signal_ids: Collection[int] | None = None,
) -> Checked:
    """Keep claims whose every cited id is allowed; drop the rest with the reason per id."""
    allowed = set(allowed)
    bad_ids = {i for c in claims for i in c.signal_ids if i not in allowed}
    why = classify_ids(session, bad_ids, entity_signal_ids=entity_signal_ids)
    out = Checked()
    for c in claims:
        bad = {i: why[i] for i in c.signal_ids if i not in allowed}
        if not c.signal_ids:
            out.drop(c, "no_citation")
        elif bad:
            out.drop(c, "invalid_citation", invalid_ids={str(k): v for k, v in bad.items()})
        else:
            out.kept.append(c)
    return out
