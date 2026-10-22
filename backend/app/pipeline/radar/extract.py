"""Radar inputs: one Observation per (signal, player) pair, read from the DB each run.

Three paths (meta.mention_origins records which one produced each mention):
- news_new_entity / hn_new_entity / github_new_entity / cncf_new_entity: enrich's new_entities of
  any enriched, relevant signal (news articles and discovery items alike);
- github_search: a relevant repository signal is a candidate by itself (open_source_project);
- cncf_landscape: a relevant CNCF landscape addition is a candidate by itself.
Irrelevant items (triage false) never produce observations. Re-reading everything each run is
cheap at these volumes and makes the step idempotent (mentions are a set of signal ids).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import RadarKind
from app.db.models import Signal
from app.pipeline.collectors.discovery import repo_url as make_repo_url
from app.pipeline.llm_steps import canonical_or_unclustered
from app.pipeline.radar.normalize import repo_slug
from app.pipeline.scope import event_time, in_scope, not_stripped


@dataclass(frozen=True)
class Observation:
    name: str
    kind: RadarKind
    signal_id: int
    source_id: int | None
    at: datetime
    origin: str
    homepage: str | None = None
    repo_url: str | None = None
    context: str | None = None
    segments: tuple[str, ...] = ()
    hn_points: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _kind(value: str | None) -> RadarKind:
    try:
        return RadarKind(value or "product")
    except ValueError:
        return RadarKind.PRODUCT


def observations(session: Session) -> list[Observation]:
    rows = session.execute(
        select(Signal.id, Signal.source_id, Signal.meta, event_time().label("at"))
        .where(in_scope(), not_stripped(), canonical_or_unclustered(), Signal.relevant.is_(True))
        .order_by(event_time(), Signal.id)
    ).all()
    out: list[Observation] = []
    for sid, source_id, meta, at in rows:
        meta = meta or {}
        segments = tuple((meta.get("triage") or {}).get("segments") or [])
        disc = meta.get("discovery") if meta.get("kind") == "discovery" else None
        points = (meta.get("hn") or {}).get("points") if disc == "hn" else None
        base = {"signal_id": sid, "source_id": source_id, "at": at, "segments": segments}

        if disc == "github" and (repo := meta.get("repo")):
            full = str(repo.get("full_name") or "")
            if full:
                out.append(
                    Observation(
                        name=str(repo.get("name") or full.split("/")[-1]),
                        kind=RadarKind.OPEN_SOURCE_PROJECT,
                        origin="github_search",
                        homepage=repo.get("homepage"),
                        repo_url=make_repo_url(*full.split("/", 1)),
                        context=None,
                        extra={"topics": repo.get("topics") or []},
                        **base,
                    )
                )
        if disc == "cncf" and (cncf := meta.get("cncf")):
            repo = cncf.get("repo_url")
            out.append(
                Observation(
                    name=str(cncf["name"]),
                    kind=RadarKind.OPEN_SOURCE_PROJECT if repo else RadarKind.PRODUCT,
                    origin="cncf_landscape",
                    homepage=cncf.get("homepage"),
                    repo_url=_repo(repo),
                    context=f"Added to the CNCF landscape ({cncf.get('category')}).",
                    **base,
                )
            )

        for e in (meta.get("enrich") or {}).get("new_entities") or []:
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            url = e.get("url")
            out.append(
                Observation(
                    name=name,
                    kind=_kind(e.get("kind")),
                    origin=f"{disc or 'news'}_new_entity",
                    homepage=url if url and repo_slug(url) is None else None,
                    repo_url=_repo(url),
                    context=e.get("context"),
                    hn_points=points,
                    **base,
                )
            )
    return out


def _repo(url: str | None) -> str | None:
    slug = repo_slug(url)
    return make_repo_url(*slug.split("/", 1)) if slug else None
