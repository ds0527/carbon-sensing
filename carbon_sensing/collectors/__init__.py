"""수집기 레지스트리. 새 어댑터는 COLLECTORS에 추가하면 파이프라인이 집어간다."""

from __future__ import annotations

from ..http import HttpFetcher
from .base import BaseCollector
from .gdelt import GdeltCollector
from .naver_news import NaverNewsCollector
from .openalex import OpenAlexCollector
from .rss import RssCollector
from .web_search import WebSearchCollector

COLLECTOR_CLASSES: dict[str, type[BaseCollector]] = {
    NaverNewsCollector.name: NaverNewsCollector,
    WebSearchCollector.name: WebSearchCollector,
    RssCollector.name: RssCollector,
    OpenAlexCollector.name: OpenAlexCollector,
    GdeltCollector.name: GdeltCollector,
}

ALL_COLLECTOR_NAMES = list(COLLECTOR_CLASSES)


def build_collectors(
    fetcher: HttpFetcher, only: list[str] | None = None
) -> list[BaseCollector]:
    names = only or ALL_COLLECTOR_NAMES
    unknown = [n for n in names if n not in COLLECTOR_CLASSES]
    if unknown:
        raise ValueError(
            f"알 수 없는 수집기: {', '.join(unknown)} "
            f"(가능한 값: {', '.join(ALL_COLLECTOR_NAMES)})"
        )
    return [COLLECTOR_CLASSES[n](fetcher) for n in names]


__all__ = [
    "BaseCollector",
    "COLLECTOR_CLASSES",
    "ALL_COLLECTOR_NAMES",
    "build_collectors",
    "NaverNewsCollector",
    "WebSearchCollector",
    "RssCollector",
    "OpenAlexCollector",
    "GdeltCollector",
]
