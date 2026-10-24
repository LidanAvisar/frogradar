"""The app's notion of "now": the wall clock (UTC).

Read paths (API, MCP, Ask, missions) call `now()` instead of datetime.now() so tests can pin time
through one seam (the API's `Now` dependency is overridden in tests).
"""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
