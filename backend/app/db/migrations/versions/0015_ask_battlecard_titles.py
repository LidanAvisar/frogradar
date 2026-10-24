"""Stored Ask answers: battlecard source titles in full (data only, S25).

Battlecard items used to be listed as sources with their text cut to 60 characters and "…".
New answers carry the whole item. This repairs stored answers: a battlecard source's id is
bc:<company slug>:<section>:<n> and its date is the creation time of the card it came from, so
the item is found again exactly. A source whose card or item is gone keeps its title.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24

"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Section order and numbering as in app.rag.documents._items when the answers were stored.
SECTIONS = ("strengths", "weaknesses", "opportunities_for_jfrog", "blind_spots_for_jfrog")


def _item_text(content: dict[str, Any], section: str, n: int) -> str | None:
    items = [content.get("positioning")] if section == "positioning" else content.get(section)
    for idx, it in enumerate(items or [], start=1):  # numbered over every entry, as _items does
        if idx == n and isinstance(it, dict) and isinstance(it.get("text"), str):
            return str(it["text"])
    return None


def _full_title(conn: sa.Connection, src: dict[str, Any]) -> str | None:
    title = src.get("title") or ""
    parts = str(src.get("id") or "").split(":")
    if not title.endswith("…") or len(parts) != 4 or parts[0] != "bc" or not parts[3].isdigit():
        return None
    _, slug, section, n = parts
    if section != "positioning" and section not in SECTIONS:
        return None
    try:
        created = datetime.fromisoformat(str(src.get("date")).replace("Z", "+00:00"))
    except ValueError:
        return None
    content = conn.execute(
        sa.text(
            "SELECT r.content FROM reports r JOIN entities e ON e.id = r.entity_id "
            "WHERE r.kind = 'battlecard' AND e.slug = :slug AND r.created_at = :created"
        ),
        {"slug": slug, "created": created},
    ).scalar()
    text = _item_text(content or {}, section, int(n))
    head, sep, _ = title.partition(": ")
    if text is None or not sep:
        return None
    return f"{head}: {text}"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, answer FROM ask_turns WHERE answer IS NOT NULL "
            "AND jsonb_typeof(answer->'sources') = 'array'"
        )
    ).all()
    for turn_id, answer in rows:
        changed = False
        for src in answer.get("sources") or []:
            if src.get("type") == "battlecard" and (full := _full_title(conn, src)):
                src["title"] = full
                changed = True
        if changed:
            conn.execute(
                sa.text("UPDATE ask_turns SET answer = CAST(:a AS jsonb) WHERE id = :id"),
                {"a": json.dumps(answer), "id": turn_id},
            )


def downgrade() -> None:
    pass  # titles stay complete; nothing to undo
