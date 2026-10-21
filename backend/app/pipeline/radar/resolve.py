"""Entity resolution for radar observations (code; embeddings only for the fuzzy check).

1. Skip what we already know (counted per reason): the name key equals the key of any entity
   name or alias (tracked or not, incl. JFrog and entities approved from the radar)
   → tracked_name; the homepage host is an entity's website host → tracked_website; the repo
   owner is a GitHub org we already collect from → tracked_org; the name is in radar.ignore
   → ignored; the key is shorter than 3 characters → too_short.
2. Attach to a live candidate (not merged): same key → exact; same repo URL → repo; same
   homepage host (shared hosts like github.com or medium.com never count) → host; else fuzzy:
   difflib ratio of the keys >= merge.name_ratio AND cosine of the name embeddings >=
   merge.embedding_cosine (both, so a near-identical spelling of a different product does not
   merge). Otherwise a new candidate.
3. Merge pass over live candidates with the same rules (a candidate created before its URL was
   known can turn out to be another one): the older survives, the newer gets merged_into_id;
   mentions, sources, segments and URLs are combined; both sides are logged in `merges`.
A dismissed candidate keeps collecting mentions (last_seen, counts) but is never reopened.
"""

import difflib
import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import RadarSettings
from app.db.enums import FetchMethod, RadarStatus
from app.db.models import Entity, RadarCandidate, Source
from app.pipeline.radar.extract import Observation
from app.pipeline.radar.normalize import MIN_KEY, name_key, repo_slug, site_host

EmbedTexts = Callable[[Sequence[str]], list[list[float]]]
DERIVED_NAME_ORIGINS = frozenset({"github_search", "cncf_landscape"})
MAX_CONTEXTS = 5


@dataclass
class Known:
    keys: set[str] = field(default_factory=set)
    hosts: set[str] = field(default_factory=set)
    orgs: set[str] = field(default_factory=set)
    ignore: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, session: Session, cfg: RadarSettings) -> "Known":
        known = cls(ignore={name_key(n) for n in cfg.ignore})
        for name, aliases, website in session.execute(
            select(Entity.name, Entity.aliases, Entity.website)
        ):
            known.keys.update(k for t in (name, *(aliases or [])) if (k := name_key(t)))
            if (h := site_host(website)) is not None:
                known.hosts.add(h)
        for url, options in session.execute(
            select(Source.url, Source.options).where(Source.fetch_method == FetchMethod.GITHUB)
        ):
            owner = url.rstrip("/").rsplit("/", 1)[-1].lower()
            known.orgs.add(owner)
            for repo in (options or {}).get("repos") or []:
                if "/" in repo:
                    known.orgs.add(repo.split("/", 1)[0].lower())
        return known

    def skip_reason(self, obs: Observation) -> str | None:
        key = name_key(obs.name)
        if len(key) < MIN_KEY:
            return "too_short"
        if key in self.ignore:
            return "ignored"
        if key in self.keys:
            return "tracked_name"
        if (h := site_host(obs.homepage)) is not None and h in self.hosts:
            return "tracked_website"
        if (slug := repo_slug(obs.repo_url)) is not None and slug.split("/")[0] in self.orgs:
            return "tracked_org"
        return None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def embed_text(name: str, kind: str, context: str | None) -> str:
    return f"{name} ({kind}): {context or ''}".strip()


class Resolver:
    def __init__(
        self,
        session: Session,
        cfg: RadarSettings,
        embed: EmbedTexts | None,
        now: datetime,
    ) -> None:
        self.session = session
        self.cfg = cfg
        self.embed = embed
        self.now = now
        self.known = Known.load(session, cfg)
        self.live: list[RadarCandidate] = list(
            session.scalars(
                select(RadarCandidate)
                .where(RadarCandidate.merged_into_id.is_(None))
                .order_by(RadarCandidate.id)
            )
        )
        self.counts: Counter[str] = Counter()
        self._vectors: dict[str, list[float]] = {}
        # Signal ids observed this run per candidate id: mentions not seen again (signal re-triaged
        # irrelevant, source out of scope) are pruned (prune()).
        self.observed: dict[int, set[int]] = {}

    # ---------- observations ----------

    def add(self, obs: Observation) -> RadarCandidate | None:
        if (reason := self.known.skip_reason(obs)) is not None:
            self.counts[f"skipped_{reason}"] += 1
            return None
        cand, how = self._match(obs)
        if cand is None:
            cand = self._create(obs)
            how = "new"
        changed = self._attach(cand, obs, how)
        self.observed.setdefault(cand.id, set()).add(obs.signal_id)
        self.counts[f"matched_{how}" if how != "new" else "created"] += 1
        if changed and how not in ("new",):
            self.counts["mentions_added"] += 1
        return cand

    def _match(self, obs: Observation) -> tuple[RadarCandidate | None, str]:
        key = name_key(obs.name)
        for c in self.live:
            if c.key == key:
                return c, "exact"
        repo = repo_slug(obs.repo_url)
        if repo is not None:
            for c in self.live:
                if repo_slug(c.repo_url) == repo:
                    return c, "repo"
        h = site_host(obs.homepage)
        if h is not None:
            for c in self.live:
                if site_host(c.homepage) == h:
                    return c, "host"
        fuzzy = self._fuzzy(key, embed_text(obs.name, obs.kind, obs.context), self.live)
        if fuzzy is not None:
            return fuzzy[0], "fuzzy"
        return None, "new"

    def _fuzzy(
        self, key: str, text: str, pool: Sequence[RadarCandidate]
    ) -> tuple[RadarCandidate, float, float] | None:
        close = [(c, r) for c in pool if (r := ratio(key, c.key)) >= self.cfg.merge.name_ratio]
        if not close or self.embed is None:
            return None
        mine = self._vector(text)
        best: tuple[RadarCandidate, float, float] | None = None
        for c, r in close:
            cos = cosine(mine, self._candidate_vector(c))
            if cos >= self.cfg.merge.embedding_cosine and (best is None or cos > best[2]):
                best = (c, r, cos)
        return best

    def _vector(self, text: str) -> list[float]:
        if text not in self._vectors:
            assert self.embed is not None
            self._vectors[text] = self.embed([text])[0]
        return self._vectors[text]

    def _candidate_vector(self, c: RadarCandidate) -> list[float]:
        if c.name_embedding is None:
            c.name_embedding = self._vector(embed_text(c.name, c.kind, _first_context(c)))
        return list(c.name_embedding)

    def _create(self, obs: Observation) -> RadarCandidate:
        cand = RadarCandidate(
            name=obs.name,
            key=name_key(obs.name),
            kind=obs.kind,
            homepage=obs.homepage,
            repo_url=obs.repo_url,
            first_seen=obs.at,
            last_seen=obs.at,
            mention_signal_ids=[],
            source_ids=[],
            segments=[],
            metrics={},
            merges=[],
            meta={},
            status=RadarStatus.NEW,
        )
        self.session.add(cand)
        self.session.flush()
        self.live.append(cand)
        return cand

    def _attach(self, cand: RadarCandidate, obs: Observation, how: str) -> bool:
        """Add the mention; returns True if it was new for this candidate."""
        new_mention = obs.signal_id not in (cand.mention_signal_ids or [])
        if new_mention:
            cand.mention_signal_ids = sorted({*(cand.mention_signal_ids or []), obs.signal_id})
        if obs.source_id is not None and obs.source_id not in (cand.source_ids or []):
            cand.source_ids = sorted({*(cand.source_ids or []), obs.source_id})
        cand.first_seen = min(cand.first_seen, obs.at)
        cand.last_seen = max(cand.last_seen, obs.at)
        cand.segments = sorted({*(cand.segments or []), *obs.segments})
        cand.homepage = cand.homepage or obs.homepage
        cand.repo_url = cand.repo_url or obs.repo_url
        meta = dict(cand.meta or {})
        # A repo / landscape name ("regshield") gives way to the name an article or post uses
        # for the same key ("Regshield").
        if (
            meta.get("name_origin") in DERIVED_NAME_ORIGINS
            and obs.origin.endswith("_new_entity")
            and name_key(obs.name) == cand.key
            and obs.name != cand.name
        ):
            cand.name = obs.name
            meta["name_origin"] = obs.origin
        meta.setdefault("name_origin", obs.origin)
        origins = dict(meta.get("mention_origins") or {})
        origins.setdefault(str(obs.signal_id), obs.origin)
        meta["mention_origins"] = origins
        contexts = dict(meta.get("contexts") or {})
        if obs.context and len(contexts) < MAX_CONTEXTS:
            contexts.setdefault(str(obs.signal_id), obs.context)
        meta["contexts"] = contexts
        if obs.name != cand.name and obs.name.casefold() != cand.name.casefold():
            names = set(meta.get("other_names") or [])
            names.add(obs.name)
            meta["other_names"] = sorted(names)
        if obs.hn_points is not None:
            meta["hn_points"] = {**(meta.get("hn_points") or {}), str(obs.signal_id): obs.hn_points}
        cand.meta = meta
        if how in ("repo", "host", "fuzzy") and obs.name != cand.name and new_mention:
            cand.merges = [
                *(cand.merges or []),
                {
                    "name": obs.name,
                    "rule": how,
                    "signal_id": obs.signal_id,
                    "at": self.now.isoformat(),
                },
            ]
        return new_mention

    # ---------- prune ----------

    def prune(self) -> int:
        """Drop mentions that no observation produced this run. A still-new candidate left with
        no mention is deleted (nothing was decided about it); decided ones are kept as they are.
        Returns candidates deleted."""
        deleted = 0
        for cand in list(self.live):
            seen = self.observed.get(cand.id, set())
            ids = [i for i in cand.mention_signal_ids or [] if i in seen]
            if ids == list(cand.mention_signal_ids or []):
                continue
            if not ids:
                if cand.status == RadarStatus.NEW:
                    self.session.delete(cand)
                    self.live.remove(cand)
                    deleted += 1
                continue
            self.counts["mentions_pruned"] += len(cand.mention_signal_ids) - len(ids)
            keep = {str(i) for i in ids}
            meta = dict(cand.meta or {})
            for k in ("mention_origins", "contexts", "hn_points"):
                meta[k] = {sid: v for sid, v in (meta.get(k) or {}).items() if sid in keep}
            cand.meta = meta
            cand.mention_signal_ids = ids
            cand.profile_signal_hash = None
        self.session.flush()
        self.counts["candidates_removed"] += deleted
        return deleted

    # ---------- merge pass ----------

    def merge_pass(self) -> int:
        """Merge live candidates that turn out to be the same player. Returns merges made."""
        merged = 0
        live = sorted(self.live, key=lambda c: (c.first_seen, c.id))
        i = 0
        while i < len(live):
            keep = live[i]
            j = i + 1
            while j < len(live):
                other = live[j]
                rule, detail = self._same(keep, other)
                if rule is not None and _mergeable(keep, other):
                    self._merge(keep, other, rule, detail)
                    live.pop(j)
                    merged += 1
                    continue
                j += 1
            i += 1
        self.live = live
        self.counts["merged"] += merged
        return merged

    def _same(self, a: RadarCandidate, b: RadarCandidate) -> tuple[str | None, dict[str, Any]]:
        if a.key == b.key:
            return "exact", {}
        ra, rb = repo_slug(a.repo_url), repo_slug(b.repo_url)
        if ra is not None and ra == rb:
            return "repo", {}
        ha, hb = site_host(a.homepage), site_host(b.homepage)
        if ha is not None and ha == hb:
            return "host", {}
        r = ratio(a.key, b.key)
        if r >= self.cfg.merge.name_ratio and self.embed is not None:
            cos = cosine(self._candidate_vector(a), self._candidate_vector(b))
            if cos >= self.cfg.merge.embedding_cosine:
                return "fuzzy", {"ratio": round(r, 3), "cosine": round(cos, 3)}
        return None, {}

    def _merge(self, keep: RadarCandidate, gone: RadarCandidate, rule: str, detail: dict) -> None:
        at = self.now.isoformat()
        keep.mention_signal_ids = sorted({*keep.mention_signal_ids, *gone.mention_signal_ids})
        keep.source_ids = sorted({*keep.source_ids, *gone.source_ids})
        keep.segments = sorted({*keep.segments, *gone.segments})
        keep.first_seen = min(keep.first_seen, gone.first_seen)
        keep.last_seen = max(keep.last_seen, gone.last_seen)
        keep.homepage = keep.homepage or gone.homepage
        keep.repo_url = keep.repo_url or gone.repo_url
        meta, other = dict(keep.meta or {}), gone.meta or {}
        for k in ("mention_origins", "contexts", "hn_points"):
            meta[k] = {**(other.get(k) or {}), **(meta.get(k) or {})}
        meta["other_names"] = sorted(
            {*(meta.get("other_names") or []), *(other.get("other_names") or []), gone.name}
        )
        keep.meta = meta
        keep.merges = [
            *(keep.merges or []),
            {"from_id": gone.id, "from_name": gone.name, "rule": rule, **detail, "at": at},
        ]
        gone.merged_into_id = keep.id
        gone.merges = [
            *(gone.merges or []),
            {"into_id": keep.id, "into_name": keep.name, "rule": rule, **detail, "at": at},
        ]
        keep.profile_signal_hash = None  # mentions changed: profile is regenerated


def _mergeable(keep: RadarCandidate, gone: RadarCandidate) -> bool:
    """Analyst decisions are never merged away: only a still-new candidate is absorbed (into
    any candidate, decided or not); two decided candidates stay apart."""
    del keep
    return gone.status == RadarStatus.NEW


def _first_context(c: RadarCandidate) -> str | None:
    contexts = (c.meta or {}).get("contexts") or {}
    return next(iter(contexts.values()), None)
