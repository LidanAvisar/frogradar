"""Small builders for embed/dedup/link tests: sources, signals, synthetic vectors, a run context."""

import hashlib
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import count

from sqlalchemy.orm import Session

from app.core.catalog import DedupSettings, EmbedSettings, PipelineFile
from app.core.llm import EmbeddingResult
from app.db.enums import EntityReviewStatus, EntityType, FetchMethod, SourceKind
from app.db.models import Entity, Signal, Source
from app.db.models.intel import EMBEDDING_DIM
from app.pipeline.context import RunContext

MODEL = "fake/embedding"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_n = count(1)


def vec(sim_to_axis: float, axis: int = 0, toward: int = 1) -> list[float]:
    """Unit vector whose cosine similarity to basis vector e[axis] is exactly sim_to_axis.

    vec(1.0, 0) = e0; vec(c, 0, 1) and vec(d, 0, 1) have similarity cos(acos(c) - acos(d)).
    Different `axis` values give unrelated topics (similarity 0).
    """
    v = [0.0] * EMBEDDING_DIM
    v[axis] = sim_to_axis
    v[toward] = math.sqrt(max(0.0, 1 - sim_to_axis**2))
    return v


def rotated(degrees: float, axis: int = 0, toward: int = 1) -> list[float]:
    """Unit vector at `degrees` from e[axis] in the (axis, toward) plane."""
    return vec(math.cos(math.radians(degrees)), axis, toward)


def cos_deg(a: float, b: float) -> float:
    return math.cos(math.radians(abs(a - b)))


def source(s: Session, name: str, trust: float = 0.7, entity: Entity | None = None) -> Source:
    src = Source(
        name=name, url=f"https://{name.lower().replace(' ', '-')}.test/feed",
        kind=SourceKind.PRESS, fetch_method=FetchMethod.RSS, trust_score=trust,
        entity_id=entity.id if entity else None,
    )  # fmt: skip
    s.add(src)
    s.flush()
    return src


def entity(
    s: Session,
    slug: str,
    name: str,
    *,
    aliases: Sequence[str] = (),
    rules: dict | None = None,
    parent: Entity | None = None,
    status: EntityReviewStatus = EntityReviewStatus.APPROVED,
) -> Entity:
    e = Entity(
        type=EntityType.PRODUCT if parent else EntityType.COMPANY, name=name, slug=slug,
        aliases=list(aliases), match_rules=rules or {}, review_status=status,
        parent_id=parent.id if parent else None,
    )  # fmt: skip
    s.add(e)
    s.flush()
    return e


def signal(
    s: Session,
    src: Source | None,
    title: str = "",
    *,
    text: str = "",
    embedding: list[float] | None = None,
    days: float = 0,
    model: str = MODEL,
) -> Signal:
    n = next(_n)
    sig = Signal(
        source_id=src.id if src else None, url=f"https://x.test/{n}",
        canonical_url=f"https://x.test/{n}",
        content_hash=hashlib.sha256(f"{n}{title}{text}".encode()).hexdigest(),
        title=title or f"signal {n}", raw_text=text or f"text {n}",
        published_at=T0 + timedelta(days=days),
        embedding=embedding, embedding_model=model if embedding is not None else None,
    )  # fmt: skip
    s.add(sig)
    s.flush()
    return sig


class FakeEmbed:
    """Deterministic synthetic vectors by title keyword; records every call."""

    def __init__(self, vectors: dict[str, list[float]] | None = None, model: str = MODEL) -> None:
        self.vectors = vectors or {}
        self.model = model
        self.calls: list[list[str]] = []

    def __call__(self, texts: Sequence[str]) -> EmbeddingResult:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            key = next((k for k in self.vectors if t.startswith(k)), None)
            # Unknown text: its own direction (hash-based), unrelated to everything else.
            axis = 100 + int(hashlib.sha256(t.encode()).hexdigest(), 16) % 1400
            out.append(self.vectors[key] if key else vec(1.0, axis=axis))
        return EmbeddingResult(out, self.model, EMBEDDING_DIM)


def ctx(
    embed: FakeEmbed | None = None,
    *,
    cross: float = 0.85,
    same: float = 0.97,
    window_days: int = 14,
    max_chars: int = 2000,
    batch_size: int = 64,
    model: str = MODEL,
) -> RunContext:
    return RunContext(
        run_id=None,
        config=PipelineFile(
            embedding=EmbedSettings(max_chars=max_chars, batch_size=batch_size),
            dedup=DedupSettings(
                window_days=window_days, cross_source_threshold=cross, same_source_threshold=same
            ),
        ),
        embedding_model=model,
        embed=embed or FakeEmbed(model=model),
    )
