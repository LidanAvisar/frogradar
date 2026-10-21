"""Dedup calibration pairs: cross-source signal pairs spread across similarity bands (no LLM).

Population: every pair of embedded signals (full catalog) from different sources, published
within the dedup window (±dedup.window_days), with cosine similarity in [0.60, 0.95). Pairs ≥ 0.95
are near-identical texts and < 0.60 are not considered duplicates by any threshold in the sweep.

Sample: ~SIZE pairs spread evenly over the 0.05-wide bands (the top pairs alone would all sit near
0.9 and leave the low thresholds without data), random within a band (fixed seed), each signal in
at most 2 sampled pairs. band_population is stored so metrics can weight each pair by how many
pairs it stands for (population estimate), not just count the sample.
"""

import random
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.eval.files import PairRow

SEED = 8
SIZE = 40
LOW, HIGH, STEP = 0.60, 0.95, 0.05
MAX_PER_SIGNAL = 2


def bands() -> list[tuple[float, float]]:
    n = round((HIGH - LOW) / STEP)
    return [(round(LOW + i * STEP, 2), round(LOW + (i + 1) * STEP, 2)) for i in range(n)]


def band_of(similarity: float) -> str | None:
    for lo, hi in bands():
        if lo <= similarity < hi:
            return f"{lo:.2f}-{hi:.2f}"
    return None


@dataclass(frozen=True)
class Pair:
    a_id: int
    b_id: int
    similarity: float


# All cross-source pairs in range, computed in Postgres (pgvector cosine distance <=>).
_PAIRS_SQL = text(
    """
    SELECT a.id AS a_id, b.id AS b_id, 1 - (a.embedding <=> b.embedding) AS similarity
    FROM signals a
    JOIN signals b
      ON a.id < b.id
     AND a.source_id IS DISTINCT FROM b.source_id
     AND abs(extract(epoch FROM a.published_at - b.published_at)) <= :window_s
    WHERE a.embedding IS NOT NULL AND b.embedding IS NOT NULL
      AND a.embedding_model = :model AND b.embedding_model = :model
      AND 1 - (a.embedding <=> b.embedding) >= :low
      AND 1 - (a.embedding <=> b.embedding) < :high
    """
)


def population(session: Session, *, window_days: int, model: str) -> list[Pair]:
    rows = session.execute(
        _PAIRS_SQL,
        {"window_s": window_days * 86400, "model": model, "low": LOW, "high": HIGH},
    ).all()
    return [Pair(r.a_id, r.b_id, float(r.similarity)) for r in rows]


def sample(pairs: list[Pair], *, size: int = SIZE, seed: int = SEED) -> list[tuple[Pair, str, int]]:
    """[(pair, band, band_population)] – even spread over bands; leftover quota goes to bands
    that still have pairs. Deterministic for the same input and seed."""
    rng = random.Random(seed)
    by_band: dict[str, list[Pair]] = {}
    for p in sorted(pairs, key=lambda p: (p.a_id, p.b_id)):
        if (b := band_of(p.similarity)) is not None:
            by_band.setdefault(b, []).append(p)
    for pool in by_band.values():
        rng.shuffle(pool)
    sizes = {b: len(v) for b, v in by_band.items()}
    used: Counter[int] = Counter()
    picked: list[tuple[Pair, str, int]] = []
    cursor = dict.fromkeys(by_band, 0)

    def next_from(band: str) -> Pair | None:
        pool = by_band[band]
        while cursor[band] < len(pool):
            p = pool[cursor[band]]
            cursor[band] += 1
            if used[p.a_id] < MAX_PER_SIGNAL and used[p.b_id] < MAX_PER_SIGNAL:
                return p
        return None

    # Round-robin over bands (low → high) until the sample is full or every band is exhausted.
    order = [f"{lo:.2f}-{hi:.2f}" for lo, hi in bands() if f"{lo:.2f}-{hi:.2f}" in by_band]
    while len(picked) < size and order:
        for band in list(order):
            if len(picked) >= size:
                break
            p = next_from(band)
            if p is None:
                order.remove(band)
                continue
            used[p.a_id] += 1
            used[p.b_id] += 1
            picked.append((p, band, sizes[band]))
    return sorted(picked, key=lambda t: (-t[0].similarity, t[0].a_id))


def to_rows(session: Session, picked: list[tuple[Pair, str, int]]) -> list[PairRow]:
    ids = sorted({i for p, _, _ in picked for i in (p.a_id, p.b_id)})
    info = {
        r.id: r
        for r in session.execute(
            text(
                "SELECT g.id, g.title, coalesce(s.name, 'research finding') AS source, "
                "g.published_at FROM signals g LEFT JOIN sources s ON s.id = g.source_id "
                "WHERE g.id = ANY(:ids)"
            ),
            {"ids": ids},
        )
    }

    def day(i: int) -> str:
        at = info[i].published_at
        return at.date().isoformat() if at else ""

    return [
        PairRow(
            a_id=p.a_id,
            b_id=p.b_id,
            a_title=info[p.a_id].title,
            b_title=info[p.b_id].title,
            a_source=info[p.a_id].source,
            b_source=info[p.b_id].source,
            a_date=day(p.a_id),
            b_date=day(p.b_id),
            similarity=round(p.similarity, 4),
            band=band,
            band_population=n,
        )
        for p, band, n in picked
    ]
