"""API test fixtures: a TestClient on the rolled-back test session, a pinned "now", row builders."""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.ask import require_ask_keys
from app.api.deps import get_now
from app.api.missions import require_mission_keys
from app.db.enums import (
    EntityReviewStatus,
    EntityType,
    FetchMethod,
    SignalCategory,
    SourceKind,
    Verification,
)
from app.db.models import Entity, Signal, Source, signal_entities
from app.db.session import get_session
from app.main import app

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
# Appears in every raw_text; no API response may contain it.
RAW_SENTINEL = "RAWTEXT-SENTINEL-7f3a"

_n = count(1)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    # Start from empty tables even if another test committed rows to the shared test DB.
    # Runs inside the outer transaction, so it is rolled back with everything else.
    db_session.execute(
        text("TRUNCATE signals, sources, entities, pipeline_runs, reports RESTART IDENTITY CASCADE")
    )
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: NOW
    # Tests run without provider keys; the LLM is faked, so the key pre-checks are skipped.
    app.dependency_overrides[require_ask_keys] = lambda: None
    app.dependency_overrides[require_mission_keys] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


class Builder:
    def __init__(self, session: Session) -> None:
        self.s = session
        self._default_source: Source | None = None

    def source(
        self,
        name: str | None = None,
        *,
        method: FetchMethod = FetchMethod.RSS,
        kind: SourceKind = SourceKind.OFFICIAL,
        active: bool = True,
        trust: float = 0.9,
    ) -> Source:
        n = next(_n)
        src = Source(
            name=name or f"Source {n}", url=f"https://src{n}.test/feed", kind=kind,
            fetch_method=method, trust_score=trust, is_active=active,
        )  # fmt: skip
        self.s.add(src)
        self.s.flush()
        return src

    @property
    def default_source(self) -> Source:
        if self._default_source is None:
            self._default_source = self.source("Default Feed")
        return self._default_source

    def entity(self, slug: str, name: str, parent: Entity | None = None, **kw: Any) -> Entity:
        e = Entity(
            slug=slug, name=name, type=EntityType.PRODUCT if parent else EntityType.COMPANY,
            parent_id=parent.id if parent else None,
            review_status=EntityReviewStatus.APPROVED, **kw,
        )  # fmt: skip
        self.s.add(e)
        self.s.flush()
        return e

    def link(self, sig: Signal, ent: Entity, primary: bool = True) -> None:
        self.s.execute(
            signal_entities.insert().values(signal_id=sig.id, entity_id=ent.id, is_primary=primary)
        )

    def signal(
        self,
        title: str | None = None,
        *,
        source: Source | None = None,
        hours_ago: float = 2,
        published: bool = True,
        fetched_hours_ago: float | None = None,
        impact: int | None = 3,
        confidence: float | None = 0.8,
        relevant: bool | None = True,
        enriched: bool = True,
        verification: Verification = Verification.SUPPORTED,
        category: SignalCategory | None = SignalCategory.FEATURE_UPDATE,
        summary: str = "A summary.",
        quote: str | None = "A verbatim quote.",
        quote_verified: bool = True,
        raw_text: str | None = None,
        meta: dict[str, Any] | None = None,
        **kw: Any,
    ) -> Signal:
        n = next(_n)
        src = source or self.default_source
        fetched = NOW - timedelta(hours=fetched_hours_ago if fetched_hours_ago is not None else 1)
        sig = Signal(
            source_id=src.id, url=f"https://x.test/{n}", canonical_url=f"https://x.test/{n}",
            content_hash=hashlib.sha256(f"sig{n}".encode()).hexdigest(),
            title=title or f"Signal {n}",
            raw_text=raw_text or f"Full article body {n}. {RAW_SENTINEL}",
            published_at=NOW - timedelta(hours=hours_ago) if published else None,
            fetched_at=fetched,
            relevant=relevant,
            category=category if enriched else None,
            summary=summary if enriched else None,
            why_jfrog_cares="Why it matters." if enriched else None,
            impact=impact if enriched else None,
            confidence=confidence if enriched else None,
            evidence_quote=quote if enriched else None,
            evidence_quote_verified=quote_verified if enriched and quote else None,
            verification=verification if enriched else Verification.PENDING,
            enriched_at=NOW - timedelta(minutes=30) if enriched else None,
            meta=meta or {},
            **kw,
        )  # fmt: skip
        self.s.add(sig)
        self.s.flush()
        return sig


@pytest.fixture
def build(db_session: Session, client: TestClient) -> Builder:
    # Depends on `client` so rows are built after the tables were emptied.
    return Builder(db_session)
