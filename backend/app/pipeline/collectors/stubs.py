"""Collectors not built yet: they raise NotImplementedCollector and the source is skipped."""

from datetime import datetime

from app.pipeline.collectors.base import (
    CollectResult,
    KnownUrls,
    NotImplementedCollector,
    SourceRef,
)


class WebCollector:
    """Generic web page collector – not planned yet."""

    def collect(
        self, source: SourceRef, *, since: datetime, backfill: bool, known: KnownUrls
    ) -> CollectResult:
        raise NotImplementedCollector("web collector not implemented")
