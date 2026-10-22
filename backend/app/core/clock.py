"""The app's notion of "now".

Read paths (API, MCP, Ask, missions) use `now()` instead of the wall clock so demo mode can pin
"today" to the snapshot's as-of date: the Brief stats, "since yesterday" and cached Ask answers
(whose prompt carries today's date) look the same whenever a reviewer opens the demo.
Writes that record when something happened (feedback, budget ledger) keep real time.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "demo" / "snapshot"
MANIFEST = SNAPSHOT_DIR / "manifest.json"


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@lru_cache
def snapshot_manifest() -> dict | None:
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return None


def snapshot_as_of() -> datetime | None:
    """The demo data's as-of time: DEMO_AS_OF, else the committed snapshot manifest."""
    s = get_settings()
    if s.demo_as_of:
        return _parse(s.demo_as_of)
    m = snapshot_manifest()
    return _parse(m["as_of"]) if m and m.get("as_of") else None


def pinned_now() -> datetime | None:
    s = get_settings()
    if s.pin_now:
        return _parse(s.pin_now)
    if s.demo_mode:
        return snapshot_as_of()
    return None


def now() -> datetime:
    return pinned_now() or datetime.now(UTC)
