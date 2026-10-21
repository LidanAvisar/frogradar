"""Collectors: one per fetch_method, all returning normalized RawItems (see base.py)."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.catalog import PageDiffSettings, load_pipeline
from app.core.config import Settings
from app.db.enums import FetchMethod
from app.pipeline.collectors.base import (
    Collector,
    CollectResult,
    KnownUrls,
    NotImplementedCollector,
    RawItem,
    SourceRef,
)
from app.pipeline.collectors.github import GitHubCollector
from app.pipeline.collectors.http import FetchError, PoliteClient, RobotsDisallowed
from app.pipeline.collectors.page_diff import PageDiffCollector
from app.pipeline.collectors.rss import RSSCollector
from app.pipeline.collectors.stubs import WebCollector


def build_collectors(
    client: PoliteClient,
    settings: Settings,
    session_factory: Callable[[], Session],
    page_diff: PageDiffSettings | None = None,
) -> dict[FetchMethod, Collector]:
    cfg = page_diff or load_pipeline().page_diff
    return {
        FetchMethod.RSS: RSSCollector(client),
        FetchMethod.GITHUB: GitHubCollector(client, token=settings.github_token),
        FetchMethod.PAGE_DIFF: PageDiffCollector(client, session_factory, cfg),
        FetchMethod.WEB: WebCollector(),
    }


__all__ = [
    "CollectResult",
    "Collector",
    "FetchError",
    "KnownUrls",
    "NotImplementedCollector",
    "PoliteClient",
    "RawItem",
    "RobotsDisallowed",
    "SourceRef",
    "build_collectors",
]
