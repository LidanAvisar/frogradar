"""Which JFrog products a generated sentence names (config names, aliases, and the name without
"JFrog " like "Curation"). Discontinued products are never matched. A bare name right after
another capitalized word ("GitHub Advanced Security") is someone else's product."""

import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import EntityType
from app.db.models import Entity

MIN_BARE_TERM = 4
MAX_PRODUCTS = 2
_PREV_WORD = re.compile(r"([A-Za-z][\w-]*)\s+$")


def product_matcher(session: Session) -> Callable[[str], list[str]]:
    company = session.scalar(
        select(Entity).where(Entity.is_self, Entity.type == EntityType.COMPANY)
    )
    if company is None:
        return lambda _text: []
    products = session.scalars(
        select(Entity).where(Entity.parent_id == company.id, Entity.discontinued_on.is_(None))
    ).all()
    full: list[tuple[str, str]] = []  # (term, product name)
    bare: list[tuple[str, str]] = []
    prefix = f"{company.name} "
    for p in products:
        full += [(t, p.name) for t in (p.name, *(p.aliases or [])) if t]
        short = p.name.removeprefix(prefix)
        if short != p.name and len(short) >= MIN_BARE_TERM:
            bare.append((short, p.name))

    def named(text: str) -> list[str]:
        hits: list[tuple[int, str]] = []
        for term, name in full:
            hits += [(m.start(), name) for m in re.finditer(rf"\b{re.escape(term)}\b", text)]
        for term, name in bare:
            for m in re.finditer(rf"\b{re.escape(term)}\b", text):
                prev = _PREV_WORD.search(text[: m.start()])
                if prev and prev.group(1)[0].isupper() and prev.group(1) != company.name:
                    continue  # "GitHub Advanced Security"
                hits.append((m.start(), name))
        out: list[str] = []
        for _, name in sorted(hits):
            if name not in out:
                out.append(name)
        return out[:MAX_PRODUCTS]

    return named
