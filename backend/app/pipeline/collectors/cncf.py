"""CNCF landscape discovery collector: projects newly added to the categories we care about.

landscape.yml (github.com/cncf/landscape) lists every project by category > subcategory. Each run
stores a flattened copy (page_snapshots, one line per project: "Category > Sub | name | homepage |
repo") when it changed, and diffs it against the previous copy. A project whose name appears in a
relevant category (discovery.yaml cncf.categories) and nowhere in the previous copy is an
addition → one signal (canonical https://landscape.cncf.io/?item=<slug>, so a project is reported
once ever). A project that only moved between categories is not an addition.

First run without history = baseline only (as for page diffs). The backfill also stores the
copies at the last commit before -90 / -60 / -30 days (commits API, 1 request each), so additions
over the window are dated to the interval they appeared in (published_at = the later copy's date).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalog import CNCFSettings
from app.db.models import PageSnapshot
from app.pipeline import snapshots
from app.pipeline.collectors.base import CollectResult, KnownUrls, RawItem, SourceRef
from app.pipeline.collectors.discovery import KIND
from app.pipeline.collectors.http import PoliteClient
from app.pipeline.normalize import canonical_url, to_utc
from app.pipeline.page_diff import text_hash

logger = logging.getLogger(__name__)

FORMAT = 1  # meta.cncf_format of stored copies; a new flattening format would not be diffed
ITEM_URL = "https://landscape.cncf.io/?item={slug}"


@dataclass(frozen=True)
class Project:
    name: str
    category: str  # "Category > Subcategory"
    homepage: str | None
    repo: str | None
    description: str | None
    crunchbase: str | None
    maturity: str | None

    @property
    def key(self) -> str:
        return name_key(self.name)

    def line(self) -> str:
        return " | ".join((self.category, self.name, self.homepage or "", self.repo or ""))


def name_key(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def parse(text: str) -> list[Project]:
    data = yaml.safe_load(text) or {}
    out: list[Project] = []
    for cat in data.get("landscape") or []:
        for sub in cat.get("subcategories") or []:
            category = f"{cat.get('name')} > {sub.get('name')}"
            for it in sub.get("items") or []:
                if not isinstance(it, dict) or not it.get("name"):
                    continue
                desc = re.sub(r"\s+", " ", str(it.get("description") or "")).strip() or None
                out.append(
                    Project(
                        name=str(it["name"]).strip(),
                        category=category,
                        homepage=it.get("homepage_url"),
                        repo=it.get("repo_url"),
                        description=desc,
                        crunchbase=it.get("crunchbase"),
                        maturity=it.get("project"),
                    )
                )
    return out


def flatten(projects: list[Project]) -> str:
    return "\n".join(sorted({p.line() for p in projects}))


def keys_of(text: str) -> set[str]:
    """Project name keys of a stored copy."""
    out = set()
    for line in text.splitlines():
        parts = line.split(" | ")
        if len(parts) >= 2:
            out.add(name_key(parts[1]))
    return out


def additions(before_text: str, after: list[Project], categories: set[str]) -> list[Project]:
    known = keys_of(before_text)
    seen: set[str] = set()
    out = []
    for p in sorted(after, key=lambda p: (p.category, p.name)):
        if p.category in categories and p.key not in known and p.key not in seen:
            seen.add(p.key)
            out.append(p)
    return out


@dataclass
class _Copy:
    captured_at: datetime
    text: str
    projects: list[Project] | None  # parsed (new copies only)
    key: str | None  # NewSnapshot key (new copies only)


class CNCFLandscapeCollector:
    def __init__(
        self,
        client: PoliteClient,
        session_factory: Callable[[], Session],
        cfg: CNCFSettings,
        *,
        token: str = "",
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.session_factory = session_factory
        self.cfg = cfg
        self.now = now
        self.headers = {"Accept": "application/vnd.github+json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        result = CollectResult()
        stored = self._stored(source.id)
        new: list[_Copy] = []
        commits = {c.meta.get("commit") for c in stored if c.meta.get("commit")}
        hashes = {c.content_hash for c in stored}

        if backfill:
            for days in sorted(self.cfg.history_days, reverse=True):
                commit = self._commit_before(self.now() - timedelta(days=days))
                if commit is None or commit[0] in commits:
                    continue
                sha, at = commit
                projects = parse(self.client.get(self.cfg.raw_at_commit.format(sha=sha)).text)
                copy = _Copy(at, flatten(projects), projects, f"cncf-{sha}")
                new.append(copy)
                commits.add(sha)
                result.snapshots.append(
                    snapshots.NewSnapshot(
                        copy.key,
                        copy.text,
                        at,
                        {"origin": "git", "commit": sha, "cncf_format": FORMAT},
                    )
                )

        projects = parse(self.client.get(source.url).text)
        self._check_categories(projects, result)
        live = _Copy(self.now(), flatten(projects), projects, "cncf-live")
        latest_text = max(
            [*((c.captured_at, c.text) for c in stored), *((c.captured_at, c.text) for c in new)],
            default=(None, None),
            key=lambda t: t[0],
        )[1]
        if live.text != latest_text and text_hash(live.text) not in hashes:
            new.append(live)
            result.snapshots.append(
                snapshots.NewSnapshot(
                    live.key, live.text, live.captured_at, {"origin": "live", "cncf_format": FORMAT}
                )
            )

        chain = sorted(
            [*(_Copy(c.captured_at, c.text, None, None) for c in stored), *new],
            key=lambda c: c.captured_at,
        )
        categories = set(self.cfg.categories)
        for before, after in zip(chain, chain[1:], strict=False):
            if after.projects is None:
                continue  # pair already diffed in an earlier run
            for p in additions(before.text, after.projects, categories):
                item = _item(p, before.captured_at, after.captured_at, after.key)
                if item.canonical_url in known:
                    result.skipped_known += 1
                    continue
                result.items.append(item)
        if not stored and len(new) == 1:
            result.notes.append("first copy stored as baseline (no history): no additions yet")
        return result

    def _stored(self, source_id: int) -> list[PageSnapshot]:
        with self.session_factory() as s:
            rows = s.scalars(
                select(PageSnapshot)
                .where(
                    PageSnapshot.source_id == source_id,
                    PageSnapshot.meta["cncf_format"].astext == str(FORMAT),
                )
                .order_by(PageSnapshot.captured_at)
            ).all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    def _commit_before(self, until: datetime) -> tuple[str, datetime] | None:
        params = {
            "path": "landscape.yml",
            "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "per_page": "1",
        }
        data = self.client.get(
            f"{self.cfg.commits_api}?{urlencode(params, quote_via=quote)}", headers=self.headers
        ).json()
        if not data:
            return None
        commit = data[0]
        at = to_utc((commit.get("commit") or {}).get("committer", {}).get("date"))
        return (str(commit["sha"]), at) if at else None

    def _check_categories(self, projects: list[Project], result: CollectResult) -> None:
        present = {p.category for p in projects}
        missing = sorted(set(self.cfg.categories) - present)
        if missing:
            result.notes.append(f"categories not in landscape.yml (renamed?): {missing}")


def _item(p: Project, before_at: datetime, after_at: datetime, after_key: str | None) -> RawItem:
    url = ITEM_URL.format(slug=slug(p.name))
    lines = [
        f"{p.name} was added to the CNCF landscape in category {p.category}.",
        f"Description: {p.description}" if p.description else "",
        f"Homepage: {p.homepage}" if p.homepage else "",
        f"Repository: {p.repo}" if p.repo else "",
        f"CNCF project maturity: {p.maturity}" if p.maturity else "",
        f"Crunchbase: {p.crunchbase}" if p.crunchbase else "",
        f"Added between {before_at.date().isoformat()} and {after_at.date().isoformat()}.",
    ]
    meta: dict[str, Any] = {
        "kind": KIND,
        "discovery": "cncf",
        "text_source": "landscape",
        "cncf": {
            "name": p.name,
            "category": p.category,
            "homepage": p.homepage,
            "repo_url": p.repo,
            "maturity": p.maturity,
            "before_captured_at": before_at.isoformat(),
            "after_captured_at": after_at.isoformat(),
        },
    }
    if after_key:
        meta["after_snapshot_key"] = after_key
    return RawItem(
        url=p.homepage or p.repo or url,
        canonical_url=canonical_url(url),
        title=f"CNCF landscape adds {p.name} ({p.category})",
        text="\n".join(line for line in lines if line),
        published_at=after_at,
        meta=meta,
    )
