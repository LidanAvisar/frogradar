"""GitHub star snapshots (one per repo per UTC day) and star velocity."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import RepoStarSnapshot


@dataclass(frozen=True)
class StarSnapshot:
    repo: str  # owner/name, lower case
    captured_at: datetime
    stars: int
    forks: int | None = None
    repo_created_at: datetime | None = None


def store(session: Session, snaps: list[StarSnapshot], run_id: int | None) -> int:
    """Upsert (the last capture of a day wins). Returns rows written."""
    if not snaps:
        return 0
    rows = {
        (s.repo, s.captured_at.date()): {
            "repo": s.repo,
            "captured_at": s.captured_at,
            "captured_day": s.captured_at.date(),
            "stars": s.stars,
            "forks": s.forks,
            "repo_created_at": s.repo_created_at,
            "run_id": run_id,
        }
        for s in snaps
    }
    stmt = insert(RepoStarSnapshot).values(list(rows.values()))
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["repo", "captured_day"],
            set_={
                "captured_at": stmt.excluded.captured_at,
                "stars": stmt.excluded.stars,
                "forks": stmt.excluded.forks,
                "run_id": stmt.excluded.run_id,
            },
        )
    )
    return len(rows)


@dataclass(frozen=True)
class Velocity:
    stars: int
    per_day: float
    # "snapshots": (latest - previous) / days between them; "age": stars / repo age in days
    # (first sighting: the repo is at most created_within_days old, so this is its average).
    basis: str
    days: float


def velocity(session: Session, repo: str, now: datetime) -> Velocity | None:
    rows = session.execute(
        select(
            RepoStarSnapshot.captured_at, RepoStarSnapshot.stars, RepoStarSnapshot.repo_created_at
        )
        .where(RepoStarSnapshot.repo == repo.lower())
        .order_by(RepoStarSnapshot.captured_at.desc())
        .limit(2)
    ).all()
    return velocity_from(rows, now)


def velocity_from(rows: list, now: datetime) -> Velocity | None:
    """rows: (captured_at, stars, repo_created_at), newest first."""
    if not rows:
        return None
    latest_at, latest_stars, created = rows[0]
    if len(rows) > 1:
        prev_at, prev_stars, _ = rows[1]
        days = (latest_at - prev_at).total_seconds() / 86400
        if days >= 0.5:
            return Velocity(
                latest_stars, max(0.0, (latest_stars - prev_stars) / days), "snapshots", days
            )
    if created is None:
        return None
    days = max(1.0, (latest_at - created).total_seconds() / 86400)
    return Velocity(latest_stars, latest_stars / days, "age", days)
