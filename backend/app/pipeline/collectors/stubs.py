"""Collectors not built yet: they raise NotImplementedCollector and the source is skipped."""

from datetime import datetime

from app.pipeline.collectors.base import (
    CollectResult,
    KnownUrls,
    NotImplementedCollector,
    SourceRef,
)


class PageDiffCollector:
    """Pricing/feature page change detection – built in S7."""

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        raise NotImplementedCollector("page_diff collector arrives in S7")


class WebCollector:
    """Generic web page collector – not planned yet."""

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        raise NotImplementedCollector("web collector not implemented")
